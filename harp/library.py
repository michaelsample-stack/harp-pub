"""The monthly library, and how a month gets into it.

    harp library                        what is on the shelf
    harp library build --month 2026-05  raw month → validate → clean → pending
    harp library promote --month 2026-05  pending → library, once a person says so

WHY A LIBRARY
-------------
A lot's chips reach back past the month the lot was made - a June lot has
already been seen reaching twelve days into May. So resolving a lot means
opening several months, and that only works if each month has one obvious file
rather than five timestamped candidates.

The outbox is a working area: run a month four times while getting it right and
all four are in there. The library holds the one that was right.

    <library>/2026-05/harvest.geojson     the geometry
    <library>/2026-05/deliveries.csv      the loads, for the walkback
    <library>/2026-05/manifest.json       what it was built from, and by whom

THREE STATES, NOT TWO
---------------------
    working    data/outbox        runs, reruns, experiments
    pending    <library>/pending  been through the cycle, waiting on a person
    library    <library>/YYYY-MM  approved

A fourth exists for the cases that will not come good on their own:

    quarantine <library>/quarantine  needs hands on it

That is not a failure state so much as an admission. Early months will contain
geometry the automated cleaner cannot fix, and a month sitting in quarantine
with its findings beside it is more useful than one that failed silently or,
worse, one that was promoted while still broken.

THE CYCLE
---------
A raw month is validated, cleaned, and validated again, up to a limit. If
Required findings remain after the last pass, it goes to quarantine rather than
pending.

    raw → validate → clean → validate → clean → validate → pending
                                                         └→ quarantine

Recommended findings never block. They are reported and carried through,
because a warning that stops a month is a warning that gets switched off.

PROMOTION IS DELIBERATE
-----------------------
Nothing reaches the library without a person saying so. `require_approval` in
config is the switch, and it defaults to on. The eventual intent is a service
that promotes clean months by itself; until the cleaner is tuned for this data
rather than for supplier submissions, a human looks first.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime

MONTH = re.compile(r"^\d{4}-\d{2}$")

# How many validate/clean rounds before giving up and asking for a person.
DEFAULT_MAX_PASSES = 3

# Passed to eudr_clean. Every one of these changes what is being asserted about
# a plot, so they are opt-in and configured rather than assumed.
DEFAULT_CLEAN = {
    "hole_mode": None,
    "fix_overlaps": False,
    "convert_degenerate_to_points": False,
    "convert_small_polygons": False,
    "remove_proximate_vertices": False,
}


def settings(cfg) -> dict:
    lib = ((getattr(cfg, "sources", None) or {}).get("library") or {})
    out = {
        "path": lib.get("path") or os.path.join(cfg.paths.outbox, "..",
                                                "library"),
        "require_approval": lib.get("require_approval", True),
        "max_passes": int(lib.get("max_passes", DEFAULT_MAX_PASSES)),
        "country_iso2": lib.get("country_iso2") or None,
        "clean": {**DEFAULT_CLEAN, **(lib.get("clean") or {})},
    }
    out["path"] = os.path.abspath(os.path.expandvars(
        os.path.expanduser(out["path"])))
    return out


def _dirs(root: str) -> dict:
    return {"root": root,
            "pending": os.path.join(root, "pending"),
            "quarantine": os.path.join(root, "quarantine")}


# ──────────────────────────────── reading ──────────────────────────────────

def months(root: str) -> list[dict]:
    """What is on the shelf, and what is waiting."""
    out = []
    if not os.path.isdir(root):
        return out
    d = _dirs(root)
    for state, base in (("library", root), ("pending", d["pending"]),
                        ("quarantine", d["quarantine"])):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not MONTH.match(name):
                continue
            path = os.path.join(base, name)
            if not os.path.isdir(path):
                continue
            man = {}
            mp = os.path.join(path, "manifest.json")
            if os.path.isfile(mp):
                try:
                    with open(mp, encoding="utf-8") as fh:
                        man = json.load(fh)
                except Exception:
                    man = {}
            out.append({"month": name, "state": state, "path": path,
                        "features": man.get("features"),
                        "approved_by": man.get("approved_by"),
                        "findings": man.get("findings_remaining"),
                        "manifest": man})
    return out


def read_month(root: str, month: str, log=print) -> list[dict]:
    """A month's geometry, from the library only.

    Deliberately does not fall back to pending. A lot resolved against an
    unapproved month would look identical to one resolved against an approved
    one, and the difference matters.
    """
    path = os.path.join(root, month, "harvest.geojson")
    if not os.path.isfile(path):
        d = _dirs(root)
        for state in ("pending", "quarantine"):
            if os.path.isdir(os.path.join(d[state], month)):
                raise FileNotFoundError(
                    "{} is in {}, not the library. It has not been approved, "
                    "so nothing should be declared from it yet.".format(
                        month, state))
        raise FileNotFoundError(
            "no month {} in the library at {}".format(month, root))
    with open(path, encoding="utf-8") as fh:
        feats = json.load(fh).get("features") or []
    log("  {:>7,} feature(s) from {}".format(len(feats), month))
    return feats


# ──────────────────────────── the validate cycle ───────────────────────────

def _split_findings(findings: list) -> tuple[list, list]:
    req = [f for f in findings if str(f.get("error_type", "")).lower()
           == "required"]
    rec = [f for f in findings if f not in req]
    return req, rec


def _summarise(findings: list, log, indent="    ") -> None:
    by = Counter("{} {}".format(f.get("error_code", "?"),
                                str(f.get("label", ""))[:52])
                 for f in findings)
    for label, n in by.most_common(8):
        log("{}{:>5}  {}".format(indent, n, label))
    if len(by) > 8:
        log("{}{:>5}  other codes".format(indent, len(by) - 8))


def cycle(features: list[dict], max_passes: int, clean_opts: dict,
          country_iso2: str | None = None, log=print) -> dict:
    """Validate, clean, validate again. Returns the outcome.

    Recommended findings never block. A warning that stops a month is a
    warning somebody switches off.
    """
    try:
        from eudr_geojson import validate_file
        from eudr_clean import clean_file
    except ImportError as exc:
        raise RuntimeError(
            "the EUDR libraries are not installed: {}.\n"
            "  pip install eudr_geojson eudr_clean".format(exc))

    coll = {"type": "FeatureCollection", "features": list(features)}
    history, passes = [], 0

    for attempt in range(max_passes + 1):
        findings = validate_file(coll, country_iso2=country_iso2)
        req, rec = _split_findings(findings)
        log("  pass {}: {:,} required, {:,} recommended".format(
            attempt, len(req), len(rec)))
        if req:
            _summarise(req, log)
        history.append({"pass": attempt, "required": len(req),
                        "recommended": len(rec)})

        if not req:
            return {"ok": True, "features": coll["features"], "passes": passes,
                    "required": 0, "recommended": len(rec),
                    "findings": rec, "history": history}

        if attempt == max_passes:
            # Out of passes with Required findings still standing. Not a
            # failure of the run - a month that needs hands on it.
            return {"ok": False, "features": coll["features"],
                    "passes": passes, "required": len(req),
                    "recommended": len(rec), "findings": req,
                    "history": history}

        log("  cleaning…")
        result = clean_file(coll, **clean_opts)
        kept = result.get("valid_features") or []
        failed = result.get("failed_features") or []
        if failed:
            log("  {:,} feature(s) the cleaner could not fix".format(
                len(failed)))
        if len(kept) == len(coll["features"]) and attempt and \
                history[-1]["required"] == history[-2]["required"]:
            # Same count twice running and nothing dropped: the cleaner has
            # nothing further to offer and more passes will not change that.
            log("  no change since the last pass - stopping early")
            return {"ok": False, "features": kept, "passes": passes,
                    "required": len(req), "recommended": len(rec),
                    "findings": req, "history": history,
                    "stalled": True}
        coll = {"type": "FeatureCollection", "features": kept}
        passes += 1

    return {"ok": False, "features": coll["features"], "passes": passes,
            "required": -1, "recommended": 0, "findings": [],
            "history": history}


# ──────────────────────────────── building ─────────────────────────────────

def build(root: str, month: str, features: list[dict], deliveries_path: str,
          opts: dict, source_files: list[str] | None = None,
          log=print) -> dict:
    """Take a raw month through the cycle and stage it.

    Lands in pending if it comes out clean, quarantine if it does not.
    """
    if not MONTH.match(month):
        raise RuntimeError("month wants YYYY-MM, got '{}'".format(month))

    log("{:,} raw feature(s) for {}".format(len(features), month))
    out = cycle(features, opts["max_passes"], opts["clean"],
                opts["country_iso2"], log=log)

    d = _dirs(root)
    state = "pending" if out["ok"] else "quarantine"
    dest = os.path.join(d[state], month)
    os.makedirs(dest, exist_ok=True)

    with open(os.path.join(dest, "harvest.geojson"), "w",
              encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "name": "harp_harvest",
                   "features": out["features"]}, fh)

    if deliveries_path and os.path.isfile(deliveries_path):
        # The loads live with the geometry. The walkback needs both, and a
        # month whose deliveries went missing is a month that cannot be used.
        shutil.copy2(deliveries_path,
                     os.path.join(dest, "deliveries" +
                                  os.path.splitext(deliveries_path)[1]))

    manifest = {
        "month": month,
        "state": state,
        "built": datetime.now().isoformat(timespec="seconds"),
        "features": len(out["features"]),
        "features_raw": len(features),
        "clean_passes": out["passes"],
        "findings_remaining": out["required"],
        "recommended_remaining": out["recommended"],
        "history": out["history"],
        "clean_options": opts["clean"],
        "source_files": source_files or [],
        "approved_by": None,
        "approved_at": None,
    }
    if out.get("stalled"):
        manifest["note"] = ("the cleaner stopped making progress before the "
                            "pass limit")
    with open(os.path.join(dest, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    if out["findings"]:
        with open(os.path.join(dest, "findings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(out["findings"], fh, indent=2)

    log("")
    if out["ok"]:
        log("{} is in pending, {:,} feature(s) after {} cleaning pass(es)."
            .format(month, len(out["features"]), out["passes"]))
        log("Nothing is declared from pending. Approve it to shelve it:")
        log("  harp library promote --month {}".format(month))
    else:
        log("{} went to quarantine with {:,} Required finding(s) still "
            "standing.".format(month, out["required"]))
        log("The findings are beside it in findings.json. This needs hands on "
            "it, not another pass.")
    return {"state": state, "path": dest, "manifest": manifest}


def promote(root: str, month: str, who: str, force: bool = False,
            log=print) -> str:
    """Move a month from pending onto the shelf."""
    d = _dirs(root)
    src = os.path.join(d["pending"], month)
    if not os.path.isdir(src):
        q = os.path.join(d["quarantine"], month)
        if os.path.isdir(q):
            if not force:
                raise RuntimeError(
                    "{} is in quarantine, not pending. It still has Required "
                    "findings.\n  Fix them, or promote with --force if you "
                    "have decided they are acceptable - which will be "
                    "recorded.".format(month))
            src = q
        else:
            raise RuntimeError("nothing pending for {}".format(month))

    dest = os.path.join(root, month)
    if os.path.isdir(dest):
        # Replacing a shelved month is a real thing to want - a rerun with
        # better data - but the old one goes somewhere recoverable first.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.move(dest, os.path.join(root, "_replaced",
                                       "{}-{}".format(month, stamp)))
        log("the previous {} was moved to _replaced".format(month))

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)

    mp = os.path.join(dest, "manifest.json")
    man = {}
    if os.path.isfile(mp):
        with open(mp, encoding="utf-8") as fh:
            man = json.load(fh)
    man["state"] = "library"
    man["approved_by"] = who
    man["approved_at"] = datetime.now().isoformat(timespec="seconds")
    if force:
        man["approved_over_findings"] = man.get("findings_remaining")
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=2)

    log("{} is on the shelf, approved by {}.".format(month, who))
    if force:
        log("Promoted over {} Required finding(s). That is recorded in the "
            "manifest.".format(man.get("findings_remaining")))
    return dest
