"""Comparing one monthly drop against the last.

A client's LIMS export arrives on a cycle and mostly repeats itself. Resolving
all 279 sources every month is wasteful and, worse, it buries the handful of
rows that actually changed - which are the only ones anyone needs to look at.

Three questions, and they have different answers:

    what is NEW          resolve it
    what CHANGED         re-resolve it, and say what moved
    what has GONE        do not silently drop it - a supplier who stopped
                         delivering is a fact about the supply chain, not an
                         absence of data

On the key
----------
The comparison keys on the client's own source id where there is one, falling
back to the identifier. An identifier alone is not unique: 'PRINCETON' appears
under both Gorman and Weyerhaeuser in the Harmac data, so keying on it reports
55 spurious changes where nothing moved at all. The source id - SOURCEID in a
LIMS export, of the form SUPPLIER-UNIT - is both stable and unique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .identify import Record


@dataclass
class Diff:
    """What changed between two drops."""

    new: list[Record] = field(default_factory=list)
    changed: list[tuple[Record, Record, list[str]]] = field(default_factory=list)
    gone: list[dict] = field(default_factory=list)
    unchanged: list[Record] = field(default_factory=list)

    @property
    def to_resolve(self) -> list[Record]:
        """What actually needs querying. Everything else is already answered."""
        return self.new + [cur for cur, _prev, _f in self.changed]

    def summary(self) -> str:
        return ("{} new  ·  {} changed  ·  {} gone  ·  {} unchanged  "
                "->  {} to resolve".format(
                    len(self.new), len(self.changed), len(self.gone),
                    len(self.unchanged), len(self.to_resolve)))


# Fields whose change matters. A change to any of these can change the answer,
# so the source is re-resolved. Anything else - a tidied supplier name, a new
# note - is cosmetic and does not justify a query.
MATERIAL = ("identifier", "jurisdiction", "product_type", "supplier_id")


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


def _row_key(source_id: Any, identifier: Any) -> str:
    """Source id when there is one, identifier otherwise."""
    sid = _key(source_id)
    return sid or _key(identifier)


def compare(current: list[Record], previous_manifest: list[dict]) -> Diff:
    """Compare this month's records against last month's resolution manifest.

    `previous_manifest` is the rows written by a prior run - the CSV or JSON
    from `outbox`. Passing the manifest rather than the previous spreadsheet is
    deliberate: it carries what we concluded, not just what we were sent.
    """
    prev: dict[str, dict] = {}
    for row in previous_manifest:
        k = _row_key(row.get("source_id"), row.get("identifier"))
        if k and k not in prev:
            prev[k] = row

    out = Diff()
    seen: set[str] = set()

    for rec in current:
        k = _row_key(rec.source_id, rec.identifier)
        if not k:
            continue
        seen.add(k)
        old = prev.get(k)
        if old is None:
            out.new.append(rec)
            continue

        moved = []
        for f in MATERIAL:
            was = _key(old.get(f))
            now = _key(getattr(rec, f, ""))
            if was and now and was != now:
                moved.append("{}: {} -> {}".format(f, was, now))
        if moved:
            out.changed.append((rec, _as_record(old), moved))
        else:
            out.unchanged.append(rec)

    for k, row in prev.items():
        if k not in seen:
            out.gone.append(row)
    return out


def _as_record(row: dict) -> Record:
    return Record(
        source_id=str(row.get("source_id") or ""),
        identifier=str(row.get("identifier") or ""),
        supplier_name=str(row.get("supplier_name") or ""),
        supplier_id=str(row.get("supplier_id") or ""),
        jurisdiction=str(row.get("jurisdiction") or ""),
        product_type=str(row.get("product_type") or ""),
        raw=row,
    )


def carry_forward(diff: Diff, previous_manifest: list[dict]) -> list[dict]:
    """Last month's answers for the sources that did not change.

    Returned so a full register can be rebuilt from a partial run: this
    month's freshly resolved rows, plus these.
    """
    prev = {_row_key(r.get("source_id"), r.get("identifier")): r
            for r in previous_manifest}
    out = []
    for rec in diff.unchanged:
        row = prev.get(_row_key(rec.source_id, rec.identifier))
        if row:
            row = dict(row)
            row["carried_forward"] = True
            out.append(row)
    return out


def gone_report(diff: Diff) -> list[dict]:
    """Sources that were in the last drop and are not in this one.

    Not an error. A supplier can stop delivering, or a mark can be retired.
    But it must be visible: a source that quietly vanishes between exports is
    how a supply chain gap goes unnoticed.
    """
    return [{
        "identifier": r.get("identifier", ""),
        "source_id": r.get("source_id", ""),
        "supplier_name": r.get("supplier_name", ""),
        "last_tier": r.get("precision_tier", ""),
        "last_class": r.get("class", ""),
        "tenure_holder": r.get("tenure_holder", ""),
        "note": "present in the previous drop, absent from this one",
    } for r in diff.gone]
