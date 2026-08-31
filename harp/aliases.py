"""The supplier alias table — decisions, not derivations.

WHY THIS EXISTS
---------------
Matching a supplier name to a tenure holder is not a problem that can be
solved by a better algorithm, because some of the answer is not in the names.
Teal-Jones Group owns Teal Cedar Products; nothing in either string says so.
Kruger Inc. and Kruger Products 2010 LP may or may not be the same firm.
Somebody has to know, and once they do, that knowledge should be written down
rather than re-derived every month by a matcher that will reach the same
uncertain conclusion each time.

So the matcher proposes and this table decides. A confirmation made in August
holds in September. Tightening the matcher does not silently change a
historical answer, because the answer is no longer coming from the matcher.

THREE STATES
------------
    accepted    this client is this supplier. Used.
    rejected    it is not. Never proposed again.
    proposed    the matcher found it; nobody has ruled. NOT used.

A proposed row is not a weaker accepted row. It is absent from any output that
feeds a declaration, because a supplier's entire tenure is thousands of blocks
and attaching the wrong company to it would be wrong rather than merely broad.

WHAT IS RECORDED
----------------
Supplier, client number, client name, state, who decided, when, and why. The
reason matters more than it looks: 'parent company, confirmed by Angela 21 Aug'
is the difference between a decision and a guess that has been sitting around
long enough to look like one.

FILE FORMAT
-----------
CSV, hand-editable, one row per supplier-client pair. It is meant to be opened
and edited by a person - that is the point of it - so it stays flat and
readable rather than becoming a database.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date

COLUMNS = ["supplier", "client_number", "client_name", "state", "decided_by",
           "decided_on", "reason", "jurisdiction", "match_basis"]

ACCEPTED = "accepted"
REJECTED = "rejected"
PROPOSED = "proposed"


@dataclass
class Alias:
    supplier: str
    client_number: str = ""
    client_name: str = ""
    state: str = PROPOSED
    decided_by: str = ""
    decided_on: str = ""
    reason: str = ""
    jurisdiction: str = "BC"
    match_basis: str = ""

    @property
    def key(self) -> tuple:
        return (self.supplier.strip().upper(),
                str(self.client_number).strip(),
                self.jurisdiction.strip().upper() or "BC")

    @property
    def usable(self) -> bool:
        return self.state == ACCEPTED

    def row(self) -> dict:
        return {c: getattr(self, c) for c in COLUMNS}


class AliasTable:
    """Every supplier-to-holder decision made, and every one still open."""

    def __init__(self, path: str):
        self.path = path
        self.rows: dict[tuple, Alias] = {}
        self.load()

    # ─────────────────────────────────────────────────────────── io

    def load(self) -> int:
        self.rows = {}
        if not os.path.isfile(self.path):
            return 0
        with open(self.path, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                a = Alias(**{c: (r.get(c) or "").strip() for c in COLUMNS
                             if c in r})
                if a.supplier:
                    self.rows[a.key] = a
        return len(self.rows)

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # accepted first, then what needs deciding, then the rejections -
        # the file is meant to be opened by a person and the open questions
        # should not be buried at the bottom
        order = {ACCEPTED: 0, PROPOSED: 1, REJECTED: 2}
        rows = sorted(self.rows.values(),
                      key=lambda a: (order.get(a.state, 3),
                                     a.supplier.upper(), a.client_name))
        with open(self.path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for a in rows:
                w.writerow(a.row())
        return self.path

    # ────────────────────────────────────────────────────── lookups

    def for_supplier(self, supplier: str, jurisdiction: str = "BC",
                     state: str | None = ACCEPTED) -> list[Alias]:
        sup = supplier.strip().upper()
        jur = (jurisdiction or "BC").strip().upper()
        return [a for a in self.rows.values()
                if a.supplier.strip().upper() == sup
                and (a.jurisdiction or "BC").strip().upper() == jur
                and (state is None or a.state == state)]

    def known(self, supplier: str, client_number: str,
              jurisdiction: str = "BC") -> Alias | None:
        return self.rows.get((supplier.strip().upper(),
                              str(client_number).strip(),
                              (jurisdiction or "BC").strip().upper()))

    def is_decided(self, supplier: str, jurisdiction: str = "BC") -> bool:
        """Has anybody ruled on this supplier at all?

        A supplier with only proposals is undecided, which is different from
        one that has been looked at and found to hold no tenure.
        """
        return any(a.state in (ACCEPTED, REJECTED)
                   for a in self.for_supplier(supplier, jurisdiction, None))

    # ───────────────────────────────────────────────────── updating

    def propose(self, supplier: str, client_number: str, client_name: str,
                basis: str = "", jurisdiction: str = "BC") -> str:
        """Record a candidate the matcher found.

        Never overwrites a decision. If somebody has already accepted or
        rejected this pair, the matcher's opinion is not wanted - that is the
        entire point of the table.
        """
        existing = self.known(supplier, client_number, jurisdiction)
        if existing and existing.state in (ACCEPTED, REJECTED):
            return existing.state
        a = Alias(supplier=supplier.strip(),
                  client_number=str(client_number).strip(),
                  client_name=client_name.strip(), state=PROPOSED,
                  jurisdiction=jurisdiction, match_basis=basis)
        self.rows[a.key] = a
        return PROPOSED

    def decide(self, supplier: str, client_number: str, accept: bool,
               who: str, reason: str = "", jurisdiction: str = "BC") -> Alias:
        key = (supplier.strip().upper(), str(client_number).strip(),
               (jurisdiction or "BC").strip().upper())
        a = self.rows.get(key) or Alias(supplier=supplier.strip(),
                                        client_number=str(client_number).strip(),
                                        jurisdiction=jurisdiction)
        a.state = ACCEPTED if accept else REJECTED
        a.decided_by = who
        a.decided_on = date.today().isoformat()
        if reason:
            a.reason = reason
        self.rows[a.key] = a
        return a

    def auto_accept(self, tier: str) -> bool:
        """Which match tiers are trustworthy enough to take without a person.

        Only an exact name. Everything else is proposed, because the cost of a
        wrong holder is a declaration over somebody else's forest and the cost
        of a proposal is somebody spending a minute on it.
        """
        return tier in ("exact", "high (exact name)")

    # ─────────────────────────────────────────────────────── report

    def summary(self) -> str:
        by_state: dict[str, int] = {}
        for a in self.rows.values():
            by_state[a.state] = by_state.get(a.state, 0) + 1
        sups = {a.supplier.upper() for a in self.rows.values()}
        decided = {a.supplier.upper() for a in self.rows.values()
                   if a.state in (ACCEPTED, REJECTED)}
        lines = ["{} row(s) across {} supplier(s)".format(len(self.rows),
                                                          len(sups))]
        for state in (ACCEPTED, PROPOSED, REJECTED):
            if by_state.get(state):
                lines.append("  {:<10} {}".format(state, by_state[state]))
        open_sups = len(sups) - len(decided)
        if open_sups:
            lines.append("  {} supplier(s) have nothing decided either "
                         "way".format(open_sups))
        return "\n".join(lines)

    def open_questions(self) -> list[Alias]:
        """What a person needs to rule on, newest proposals first."""
        return sorted([a for a in self.rows.values() if a.state == PROPOSED],
                      key=lambda a: (a.supplier.upper(), a.client_name))
