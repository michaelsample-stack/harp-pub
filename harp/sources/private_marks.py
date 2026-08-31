"""The BC private-land timber mark registry.

WHAT THIS IS
------------
A registry, not a client file. The extracts arrive in Harmac's monthly dump
but they are a BC-wide provincial scaling return: 1,907 marks across 23
districts, of which about 40 are Harmac's. The rest belong to companies with
no connection to this engagement.

So it sits here beside FTEN and HBS - something HARP looks things up in -
rather than in a client's inbox. One consequence matters: the resolved
parcels are shared. A mark resolved for Harmac in August is free for Magnum
in September.

WHAT IT ANSWERS
---------------
For a private mark that FTEN and HBS both leave without geometry:

    timber mark  ->  PID  ->  ParcelMap BC  ->  parcel polygon

WHAT A PARCEL IS NOT
--------------------
The ownership boundary, not the cutblock. A 200 ha parcel behind a 12 ha cut
over-declares by sixteen times, and over-declaring asserts deforestation-free
status over land we have no evidence about.

So a parcel is returned as a SEARCH AREA, not an answer. The harvest inside it
is found by change detection - see `harp.detect`. Until that step runs, a
parcel-derived result is P3: bounded, real, and not a plot.

THE EXTRACTS ARE NOT CUMULATIVE
-------------------------------
The most recently processed file covers 27.7% of the year. Every file must be
unioned; taking the newest discards three quarters of the data. `bcparcel`
handles that, and deduplicates on (mark, PID).

DEPENDENCY, NOT VENDORED
------------------------
`bcparcel` is installed, not copied in. `eudr_geojson` is currently vendored
three times inside tracemark-eo, and that is how the Billerud instance ended
up running a stale copy with the profile-filtering bug. Once is enough.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# Routes bcparcel assigns, and what HARP can do with each.
ROUTE_NOTES = {
    "pmbc_pid": "parcel id present - ParcelMap BC, free",
    "note_pid": "parcel id recovered from the NOTE column - ParcelMap BC, free",
    "pmbc_plan": ("survey plan number only - coarser, a plan covers every "
                  "parcel it created, and is only unique within a land district"),
    "federal_clss": ("reserve land - never enters the BC title register, so no "
                     "PID will ever exist. Geometry is federal, in NRCan CLSS"),
    "treaty_check": ("treaty settlement land - held in fee simple and may have "
                     "a real PID. Test before writing off as reserve"),
    "ltsa_paid": "land title number - a different registry, and a paid lookup",
    "legal_only": "only a prose description - manual review",
    "no_plot": ("blanket authority. One mark covering a whole class of land - "
                "every provincial highway right-of-way, or every road held by "
                "a municipality. No plot-level answer exists at any price. "
                "These carry real scaled volume: either it is excluded from "
                "what is declared, or the supplier's own harvest records are "
                "obtained. Structural gap, not a data-cleaning oversight"),
    "none": "nothing to resolve",
}

# Routes that yield geometry without a further decision or a bill
FREE_ROUTES = ("pmbc_pid", "note_pid", "pmbc_plan")


class NotInstalled(RuntimeError):
    """bcparcel is not available."""

    def __init__(self):
        super().__init__(
            "bcparcel is not installed. It resolves BC private timber marks "
            "to parcel geometry.\n"
            "  pip install bcparcel      (or install from the NGIS index)\n"
            "Without it, private marks stop at the district and stay P4.")


@dataclass
class MarkRecord:
    """What the registry knows about one timber mark."""

    timber_mark: str
    pids: list[str] = field(default_factory=list)
    route: str = ""
    districts: list[str] = field(default_factory=list)
    legal: str = ""
    inferred_pids: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def resolvable(self) -> bool:
        return self.route in FREE_ROUTES and bool(self.pids)

    @property
    def route_note(self) -> str:
        return ROUTE_NOTES.get(self.route, self.route)


class Registry:
    """The private mark table, built once and consulted per source.

    Built from the extract folder on first use, then held. Parcel geometry is
    fetched lazily - only for marks a job list actually asks about, because
    1,907 marks are in the extracts and a client uses a few dozen.
    """

    def __init__(self, extracts_dir: str | None = None,
                 cache_dir: str = "./data/cache/bcparcel", log=None):
        self.extracts_dir = extracts_dir
        self.cache_dir = cache_dir
        self.log = log or (lambda *_: None)
        self._marks: dict[str, MarkRecord] = {}
        self._built = False
        self._stats: Any = None
        # One session for the life of the registry. A fresh one per mark cost
        # a TLS handshake per mark, which on a few dozen marks is most of the
        # wall clock and none of the work.
        self._session = None
        # A PID the service has no parcel for is a fact worth keeping. Without
        # it, every wrong inference is re-requested on every run forever.
        self._missing: set[str] = set()
        self._missing_loaded = False

    # ------------------------------------------------------------- building

    def _stage(self) -> str | None:
        """Copy the extracts, and only the extracts, somewhere of their own.

        The folder handed over is a whole monthly drop - a supply list, a
        delivery record and two passports sit alongside the extracts. bcparcel
        reads every workbook in a directory, so pointing it at the drop makes
        it try to read a supply list as a timber mark extract and fail on the
        missing column.

        Files are selected by the same column signatures the package sorter
        uses, so a new file type appearing in the drop cannot break this.
        """
        import shutil
        from .. import package

        found = []
        for name in sorted(os.listdir(self.extracts_dir)):
            path = os.path.join(self.extracts_dir, name)
            if not os.path.isfile(path) or name.startswith("~$"):
                continue
            if not name.lower().endswith((".xlsx", ".xlsm")):
                continue
            if package.classify(path).kind == "private_marks":
                found.append(path)

        if not found:
            self.log("no timber mark extracts in {} - nothing to index".format(
                self.extracts_dir))
            return None

        staged = os.path.join(self.cache_dir, "extracts")
        os.makedirs(staged, exist_ok=True)
        for old in os.listdir(staged):
            try:
                os.remove(os.path.join(staged, old))
            except OSError:
                pass
        for path in found:
            shutil.copy2(path, staged)
        skipped = len([n for n in os.listdir(self.extracts_dir)
                       if n.lower().endswith((".xlsx", ".xlsm"))]) - len(found)
        self.log("{} timber mark extract(s) selected{}".format(
            len(found), ", {} other workbook(s) left alone".format(skipped)
            if skipped > 0 else ""))
        return staged

    def build(self, force: bool = False) -> int:
        """Read the extracts and index every mark. Returns the mark count."""
        if self._built and not force:
            return len(self._marks)
        if not self.extracts_dir or not os.path.isdir(self.extracts_dir):
            self.log("no private mark extracts configured - private marks will "
                     "stop at the district")
            self._built = True
            return 0

        try:
            import bcparcel
        except ImportError as exc:
            raise NotInstalled() from exc

        os.makedirs(self.cache_dir, exist_ok=True)
        staged = self._stage()
        if staged is None:
            self._built = True
            return 0

        tables_dir = os.path.join(self.cache_dir, "tables")
        self.log("building the private mark registry")
        self._stats = bcparcel.run_extract(staged, tables_dir, log=self.log)
        self._index(tables_dir)
        self._built = True
        return len(self._marks)

    def _index(self, tables_dir: str) -> None:
        """Fold bcparcel's routed tables into one lookup keyed on mark."""
        import csv

        def rows(name):
            p = os.path.join(tables_dir, name)
            if not os.path.exists(p):
                return []
            with open(p, encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))

        for r in rows("pids_clean.csv"):
            mark = (r.get("TIMBER_MARK") or "").strip().upper()
            if not mark:
                continue
            m = self._marks.setdefault(mark, MarkRecord(timber_mark=mark))
            pid = (r.get("PID") or r.get("VALUE") or "").strip()
            if pid and pid not in m.pids:
                m.pids.append(pid)
                if str(r.get("INFERRED", "")).strip().lower() in ("true", "1"):
                    m.inferred_pids.append(pid)
            m.route = m.route or (r.get("ROUTE") or "pmbc_pid")
            d = (r.get("ORG_UNIT_CODE") or "").strip()
            if d and d not in m.districts:
                m.districts.append(d)
            m.legal = m.legal or (r.get("LEGAL") or "")

        # Marks with no PID still matter: knowing a mark is a blanket
        # authority is an answer, and a different one from "not found".
        for name in ("legal_fallback.csv", "title_numbers.csv",
                     "unclassified.csv"):
            for r in rows(name):
                mark = (r.get("TIMBER_MARK") or "").strip().upper()
                if not mark or mark in self._marks:
                    continue
                m = MarkRecord(timber_mark=mark,
                               route=(r.get("ROUTE") or "").strip(),
                               legal=(r.get("LEGAL") or ""),
                               note=(r.get("NOTE") or ""))
                d = (r.get("ORG_UNIT_CODE") or "").strip()
                if d:
                    m.districts.append(d)
                self._marks[mark] = m

    # ------------------------------------------------------------- lookups

    def get(self, mark: str) -> MarkRecord | None:
        if not self._built:
            self.build()
        return self._marks.get(str(mark or "").strip().upper())

    def __contains__(self, mark: str) -> bool:
        return self.get(mark) is not None

    def __len__(self) -> int:
        return len(self._marks)

    # ------------------------------------------------------------- parcels

    def _parcel_cache_path(self, pid: str) -> str:
        d = os.path.join(self.cache_dir, "parcels")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "{}.json".format(pid))

    def cached_parcel(self, pid: str) -> dict | None:
        p = self._parcel_cache_path(pid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _missing_path(self) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        return os.path.join(self.cache_dir, "no_parcel.txt")

    def _load_missing(self) -> set[str]:
        """PIDs the service has already said it has nothing for.

        Kept separately from the parcel cache because an absence is not a
        parcel. A PID lands here when a batch containing it comes back without
        it - which usually means the inference that produced it was wrong, and
        that is worth remembering rather than rediscovering monthly.
        """
        if self._missing_loaded:
            return self._missing
        self._missing_loaded = True
        p = self._missing_path()
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    self._missing = {ln.strip() for ln in fh if ln.strip()}
            except Exception:
                self._missing = set()
        return self._missing

    def _remember_missing(self, pids) -> None:
        new = {p for p in pids if p not in self._load_missing()}
        if not new:
            return
        self._missing |= new
        try:
            with open(self._missing_path(), "a", encoding="utf-8") as fh:
                for p in sorted(new):
                    fh.write(p + "\n")
        except Exception:
            pass

    def forget_missing(self) -> int:
        """Clear the not-found list, so every PID is tried again.

        Worth doing when the parcel fabric has been republished, or when a
        PID-reading bug has been fixed - both make an old absence untrue.
        """
        n = len(self._load_missing())
        self._missing = set()
        try:
            os.remove(self._missing_path())
        except OSError:
            pass
        return n

    def parcels(self, mark: str) -> tuple[list[dict], list[str]]:
        """Parcel polygons for one mark. Returns (features, missing_pids).

        Cached per PID rather than per mark, deliberately: two marks often
        share a parcel, and the cache is shared across clients. A parcel
        fetched for Harmac in August costs nothing for Magnum in September.
        """
        rec = self.get(mark)
        if rec is None or not rec.pids:
            return [], []

        known_missing = self._load_missing()
        feats, need, skipped = [], [], 0
        for pid in rec.pids:
            hit = self.cached_parcel(pid)
            if hit is not None:
                feats.append(hit)
            elif pid in known_missing:
                # Already asked, already told there is nothing. Asking again
                # costs a slot in a batch and returns the same answer.
                skipped += 1
            else:
                need.append(pid)

        if need:
            feats.extend(self._fetch(need, rec))
            got = {str((f.get("properties") or {}).get("PID", "")).strip()
                   for f in feats}
            self._remember_missing([p for p in need if p not in got])
        got = {str((f.get("properties") or {}).get("PID", "")).strip()
               for f in feats}
        missing = [p for p in rec.pids if p not in got]
        return feats, missing

    def _fetch(self, pids: list[str], rec: MarkRecord) -> list[dict]:
        try:
            from bcparcel import pmbc
        except ImportError as exc:
            raise NotInstalled() from exc
        import requests

        if self._session is None:
            self._session = requests.Session()
        session = self._session
        out = []
        # Smaller than it was. A 150-PID IN clause is a slow query on this
        # service and one failure lost the whole chunk; sixty answers faster
        # and fails smaller.
        for i in range(0, len(pids), 60):
            chunk = pids[i:i + 60]
            try:
                got = pmbc.fetch_batch(chunk, session, log=self.log)
            except Exception as exc:
                self.log("  parcel fetch failed for {}: {}".format(
                    rec.timber_mark, str(exc)[:80]))
                continue
            for f in got:
                props = f.get("properties") or {}
                pid = str(props.get("PID", "")).strip()
                if not pid:
                    continue
                # An inferred PID that resolves confirms the inference. One
                # that does not is evidence the inference was wrong, not that
                # the service failed - so the distinction is carried.
                props["harp_pid_inferred"] = pid in rec.inferred_pids
                props["harp_timber_mark"] = rec.timber_mark
                props["harp_route"] = rec.route
                props["harp_geometry_means"] = (
                    "titled parcel the timber was scaled from - a search area, "
                    "not the harvest boundary")
                feat = {"type": "Feature", "geometry": f.get("geometry"),
                        "properties": props}
                try:
                    with open(self._parcel_cache_path(pid), "w",
                              encoding="utf-8") as fh:
                        json.dump(feat, fh)
                except Exception:
                    pass
                out.append(feat)
        return out

    # ------------------------------------------------------------- summary

    def summary(self) -> str:
        if not self._built:
            return "registry not built"
        if not self._marks:
            return "registry empty - no extracts configured"
        by_route: dict[str, int] = {}
        for m in self._marks.values():
            by_route[m.route or "?"] = by_route.get(m.route or "?", 0) + 1
        lines = ["{} marks indexed".format(len(self._marks))]
        for route, n in sorted(by_route.items(), key=lambda kv: -kv[1]):
            lines.append("  {:<14} {:>5}   {}".format(
                route, n, ROUTE_NOTES.get(route, "")[:60]))
        cached = 0
        d = os.path.join(self.cache_dir, "parcels")
        if os.path.isdir(d):
            cached = len(os.listdir(d))
        lines.append("  {} parcels already cached".format(cached))
        miss = len(self._load_missing())
        if miss:
            lines.append("  {} PID(s) known to have no parcel - not asked "
                         "again".format(miss))
        return "\n".join(lines)
