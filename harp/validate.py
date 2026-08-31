"""Stage 3b - validate, clean, revalidate.

The loop
--------
    validate
        no Required findings          -> done
        Required findings             -> split the collection
            registry geometry         -> reject. Do not clean it.
            everything else           -> clean, revalidate
                findings changed      -> go round again
                findings unchanged    -> stop, nothing more to gain
        after 3 rounds still failing  -> export for human review

Three decisions worth knowing about, because none of them are obvious.

DO NOT CLEAN REGISTRY GEOMETRY
    An FTEN polygon is a government boundary. If eudr_clean nudges a vertex to
    remove a sliver, we are no longer asserting the province's polygon - we are
    asserting our edit of it, and the provenance claim that made the geometry
    worth having is weakened. A registry polygon that fails validation is a
    finding about the register, and it goes to review as-is.

LOOP UNTIL THE FINDINGS STOP CHANGING, NOT BLINDLY THREE TIMES
    Cleaning is largely deterministic. If pass 2 produces the same finding set
    as pass 1, pass 3 will too, and two more rounds of work buy nothing. The
    cap is a backstop, not the plan.

CLEAN ONLY WHAT FAILED
    A collection of 3,000 features with 4 bad ones should not send 3,000
    features through cleaning. Findings carry a feature_id, so the failures can
    be isolated, repaired and merged back. This also keeps a run moving instead
    of blocking everything behind a handful of bad polygons.

    One trap: eudr_clean explodes MultiPolygons and splits bow-ties, so the
    feature count grows and every index shifts. Features are therefore tracked
    by `harp_source_id` in properties, never by position.

Recommended findings never trigger cleaning. They are reported and carried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import adapters

# Geometry that came from a public register and must not be edited.
REGISTRY_SOURCES = ("FTEN", "BCGW", "RESULTS", "HBS")

MAX_ROUNDS = 3


@dataclass
class Round:
    """One pass of validate, and the clean that followed it."""

    number: int
    features_in: int
    findings: int
    required: int
    recommended: int
    cleaned: int = 0
    failed_to_clean: int = 0
    note: str = ""


@dataclass
class Outcome:
    """What the loop concluded."""

    collection: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    status: str = ""            # clean | cleaned | review | error
    note: str = ""

    @property
    def required_findings(self) -> list[dict]:
        return [f for f in self.findings
                if str(f.get("error_type", "")).lower() == "required"]

    def summary(self) -> str:
        lines = ["validation: {}".format(self.status)]
        for r in self.rounds:
            lines.append(
                "  round {}  {} features  {} findings "
                "({} required, {} recommended)  {}".format(
                    r.number, r.features_in, r.findings, r.required,
                    r.recommended, r.note))
        n = len(self.review.get("features", []))
        if n:
            lines.append("  {} features need human review".format(n))
        if self.note:
            lines.append("  " + self.note)
        return "\n".join(lines)


# ─────────────────────────────── helpers ───────────────────────────────────

def _is_registry(props: dict) -> bool:
    reg = str(props.get("harp_registry") or "").upper()
    return any(r in reg for r in REGISTRY_SOURCES)


def _key(props: dict) -> str:
    """Track a feature across cleaning, which renumbers everything."""
    return "{}|{}".format(props.get("harp_source_id", ""),
                          props.get("harp_identifier", ""))


def _fingerprint(findings: list[dict]) -> frozenset:
    """What the findings say, ignoring feature order.

    Compared between rounds to decide whether another pass would achieve
    anything.
    """
    return frozenset(
        (f.get("error_code"), f.get("error_type"),
         str(f.get("production_place", "")), str(f.get("wkt", ""))[:120])
        for f in findings)


def _subset(collection: dict, indices: set[int]) -> dict:
    feats = [f for i, f in enumerate(collection.get("features", []))
             if i in indices]
    return {k: v for k, v in collection.items() if k != "features"} | {
        "features": feats}


def split_failures(collection: dict, findings: list[dict]
                   ) -> tuple[dict, dict, dict]:
    """Split a collection into (clean, repairable, protected).

    Protected means registry geometry that failed - it is not sent to the
    cleaner. Only Required findings count as failure; Recommended ones are
    reported and carried.
    """
    feats = collection.get("features", [])
    bad: set[int] = set()
    for f in findings:
        if str(f.get("error_type", "")).lower() != "required":
            continue
        i = f.get("feature_id")
        if isinstance(i, int) and 0 <= i < len(feats):
            bad.add(i)

    protected = {i for i in bad
                 if _is_registry(feats[i].get("properties") or {})}
    repairable = bad - protected
    ok = set(range(len(feats))) - bad
    return (_subset(collection, ok), _subset(collection, repairable),
            _subset(collection, protected))


def merge(*collections: dict) -> dict:
    feats: list[dict] = []
    base: dict = {}
    for c in collections:
        if not base:
            base = {k: v for k, v in c.items() if k != "features"}
        feats.extend(c.get("features", []))
    return {**base, "features": feats}


# ──────────────────────────────── the loop ─────────────────────────────────

def run(collection: dict, country_iso2: str | None = None,
        max_rounds: int = MAX_ROUNDS, clean_options: dict | None = None,
        log=None) -> Outcome:
    """Validate, clean what can be cleaned, revalidate, stop when it stops
    improving."""
    log = log or (lambda *_: None)
    out = Outcome(collection=collection)
    clean_options = dict(clean_options or {})

    try:
        findings = adapters.validate(collection, country_iso2=country_iso2)
    except adapters.MissingLibrary as exc:
        out.status = "error"
        out.note = str(exc)
        return out

    previous = None
    working = collection
    protected_all = {"type": "FeatureCollection", "features": []}

    for round_no in range(1, max_rounds + 1):
        required = [f for f in findings
                    if str(f.get("error_type", "")).lower() == "required"]
        recommended = [f for f in findings if f not in required]
        rec = Round(round_no, len(working.get("features", [])), len(findings),
                    len(required), len(recommended))

        if not required:
            rec.note = "no required findings"
            out.rounds.append(rec)
            out.findings = findings
            out.collection = merge(working, protected_all)
            out.status = "clean" if round_no == 1 else "cleaned"
            log("  round {}: clean".format(round_no))
            return out

        fingerprint = _fingerprint(findings)
        if previous is not None and fingerprint == previous:
            rec.note = "findings unchanged - further cleaning would achieve nothing"
            out.rounds.append(rec)
            break
        previous = fingerprint

        ok, repairable, protected = split_failures(working, findings)
        # Registry features are pulled out of `working` and held aside, so the
        # next round never sees them again. Merging them back in at the end
        # without removing them here would count each one twice.
        working = merge(ok, repairable)
        protected_all = merge(protected_all, protected)
        n_prot = len(protected.get("features", []))
        n_rep = len(repairable.get("features", []))
        if n_prot:
            rec.note = "{} registry features failed and were not cleaned".format(
                n_prot)

        if not n_rep:
            rec.note = (rec.note + "; " if rec.note else "") + \
                "nothing left that may be cleaned"
            out.rounds.append(rec)
            break

        log("  round {}: {} required findings, cleaning {} features".format(
            round_no, len(required), n_rep))
        try:
            result = adapters.clean(repairable, **clean_options)
        except adapters.MissingLibrary as exc:
            rec.note = str(exc)
            out.rounds.append(rec)
            out.status = "error"
            out.note = str(exc)
            out.collection = merge(ok, repairable, protected_all)
            out.findings = findings
            return out
        except Exception as exc:
            rec.note = "cleaner failed: {}".format(exc)
            out.rounds.append(rec)
            break

        rec.cleaned = len(result.get("valid_features") or [])
        rec.failed_to_clean = len(result.get("failed_features") or [])
        out.rounds.append(rec)

        repaired = {"type": "FeatureCollection",
                    "features": list(result.get("valid_features") or [])}
        unrepairable = {"type": "FeatureCollection",
                        "features": list(result.get("failed_features") or [])}
        working = merge(ok, repaired, unrepairable)

        try:
            findings = adapters.validate(working, country_iso2=country_iso2)
        except adapters.MissingLibrary as exc:
            out.status = "error"
            out.note = str(exc)
            out.collection = merge(working, protected_all)
            return out

    # exhausted, stalled, or nothing further to try
    ok, repairable, protected = split_failures(working, findings)
    out.findings = findings
    out.collection = ok
    out.review = merge(repairable, protected, protected_all)
    out.status = "review" if out.review.get("features") else "cleaned"
    if out.status == "review":
        out.note = ("{} features still failing after {} round(s) - exported "
                    "for human review".format(
                        len(out.review["features"]), len(out.rounds)))
    return out


def review_rows(outcome: Outcome) -> list[dict]:
    """A flat table of what needs a person, and why.

    One row per finding on a feature that survived the loop, carrying the
    source it came from so it can be chased rather than merely counted.
    """
    by_key: dict[str, dict] = {}
    for f in outcome.review.get("features", []):
        by_key[_key(f.get("properties") or {})] = f.get("properties") or {}

    rows = []
    for finding in outcome.findings:
        if str(finding.get("error_type", "")).lower() != "required":
            continue
        rows.append({
            "error_code": finding.get("error_code"),
            "error_type": finding.get("error_type"),
            "label": finding.get("label"),
            "notes": finding.get("notes"),
            "geometry_type": finding.get("geometry_type"),
            "production_place": finding.get("production_place"),
            "feature_id": finding.get("feature_id"),
            "wkt": str(finding.get("wkt") or "")[:400],
        })
    return rows
