"""Resolution outcomes.

`router.Path` says how we went looking. This module says what we found. They
are different axes and both matter: a supplier can be on the `ften_public`
path and still end at a district boundary rather than a cut block.

The precision tier is the load-bearing idea. Every resolved source carries one,
so a consumer can filter on what it actually got rather than assume. A district
and a cut block are both legitimate outputs of this pipeline; treating them as
equivalent is what makes a due diligence statement indefensible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

ATTRIBUTION = ("Contains information licensed under the "
               "Open Government Licence - British Columbia.")


class Tier(str, Enum):
    """How tightly the harvest area is bounded.

    Two letters where a tier splits. In P1 they separate public forest land
    from private titled land, because the registers behind them are different.
    In P2 and P3 they separate before and after change detection - the only
    thing in the scheme that changes after a run.

    P4 is the grease trap: everything with no geometry at all. It holds
    identifiers nobody has explained, supplier codes with no company behind
    them, blanket authorities covering a whole class of land, and material
    that is out of scope. Sourcing for all of it is to be determined, and it
    stays visible rather than being quietly dropped.
    """

    P1A = "P1a"  # harvest block, from a public forest register
    P1B = "P1b"  # titled parcel the mark was scaled from - a search area
    P1C = "P1c"  # a harvest detected within one, carrying that mark
    P2A = "P2a"  # registered harvest area attributable to a supplier, undated
    P2B = "P2b"  # the same, confirmed within the associated timeline
    P3A = "P3a"  # a search area - district, county, national forest
    P3B = "P3b"  # a harvest detected within one, attributed to a supplier
    P4 = "P4"    # nothing resolved

    @property
    def label(self) -> str:
        return {
            "P1a": "harvest block, public forest register",
            "P1b": "titled parcel the mark was scaled from",
            "P1c": "detected within a titled parcel, carrying its mark",
            "P2a": "registered harvest area attributable to a supplier",
            "P2b": "the same, confirmed within the timeline",
            "P3a": "search area",
            "P3b": "detected within a search area",
            "P4": "unresolved",
        }[self.value]

    @property
    def traceability(self) -> str:
        """How the geometry was reached, in one word.

        A description of method rather than a verdict. Whether a given tier
        satisfies a regulatory test is a judgement for whoever makes the
        declaration, and the pipeline should not pre-empt it.

            direct     tied to the fibre by an identifier on the delivery
                       itself - a timber mark, and the ground it names
            indirect   a registered harvest area attributable to the supplier,
                       but reached through the company rather than through the
                       delivery
            inferred   an area, or a detection within one. Nothing links this
                       ground to this supplier except overlap
            none       no geometry
        """
        if self in (Tier.P1A, Tier.P1B, Tier.P1C):
            return "direct"
        if self in (Tier.P2A, Tier.P2B):
            return "indirect"
        if self in (Tier.P3A, Tier.P3B):
            return "inferred"
        return "none"

    @property
    def is_detected(self) -> bool:
        """Whether change detection produced this."""
        return self in (Tier.P1C, Tier.P2B, Tier.P3B)


# The old five-tier scheme, for reading anything written before 26 Aug 2026.
# P2 split on whether the resolution was an envelope; P3 covered both a titled
# parcel and a constrained catchment, which are not the same kind of thing.
LEGACY_TIERS = {
    "P1": "P1a",
    "P2": "P2a",
    "P2a": "P1a",   # old P2a was an authority polygon - a genuine plot
    "P2b": "P2a",   # old P2b was an envelope, not the new detection-confirmed
    "P2c": "P2b",   # the short-lived P2c, now P2b
    "P3": "P1b",    # or P3a where it was a catchment - see upgrade_tier
    "P4": "P3a",
    "P5": "P4",
}


def upgrade_tier(value: str, is_envelope: bool = False,
                 legacy: bool = True) -> str:
    """Read a tier written under the old scheme as one under the new.

    `legacy` must be stated. Two old values - `P2b` and `P4` - are also valid
    new values meaning something else entirely: old P2b was an envelope reached through the holder, new P2b is a detection-confirmed block. Guessing from the string
    alone would silently promote one to the other, so the caller says which
    scheme the file was written under.

    `P3` is the awkward one. It meant a titled parcel *or* a constrained
    catchment, which are not the same kind of thing. An envelope resolution
    means it was the catchment.
    """
    if not legacy:
        return value
    if value == "P3":
        return "P3a" if is_envelope else "P1b"
    return LEGACY_TIERS.get(value, value)


class Klass(str, Enum):
    """How many parties stand between the record and a harvest area.

    Decided by two questions, neither client-specific: does the record carry a
    harvest identifier, and does a public register hold geometry for that
    tenure type. Jurisdiction answers the second; it is not itself a class.
    """

    A = "A"      # harvest identifier, public register
    B = "B"      # harvest identifier, no public geometry - private land
    C1 = "C1"    # intermediary processing the client's own material
    C2 = "C2"    # third-party processor, one tier back
    D = "D"      # aggregation point, two or more tiers back
    E = "E"      # not a harvest identifier
    NA = "N/A"   # internal to the client


class IdShape(str, Enum):
    """Candidate readings of an identifier. Ranked, never eliminated."""

    TIMBER_MARK = "timber mark"
    LICENCE = "licence number"
    CUTTING_PERMIT = "cutting permit"
    DRYLAND_SORT = "dryland sort id"
    PLACE_NAME = "place or facility name"
    COMPANY_NAME = "company name or delivery mode"
    UNKNOWN = "unrecognised"


@dataclass
class Attempt:
    """One rung of the ladder, hit or miss.

    Misses are kept deliberately. "We checked six registers and it was not
    there" is a finding a client can act on; an empty result is not.
    """

    rung: str
    registry: str
    query: str
    hit: bool
    detail: str = ""
    at: str = field(default_factory=lambda:
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class Resolution:
    """What HARP established about one source record."""

    source_id: str
    identifier: str
    supplier_name: str = ""
    supplier_id: str = ""
    jurisdiction: str = ""
    product_type: str = ""

    klass: Klass | None = None
    path: str = ""                 # router.Path value
    tier: Tier = Tier.P4
    shapes: list[IdShape] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)

    features: list[dict] = field(default_factory=list)
    matched_rung: str = ""
    matched_field: str = ""
    registry: str = ""

    verdict: str = ""              # CROWN / PRIVATE / UNCLEAR / NOT FOUND
    verdict_basis: str = ""
    land_type: str = ""            # resolved, never declared - see below
    tenure_holder: str = ""
    client_number: str = ""
    client_location: str = ""
    district_code: str = ""
    district_name: str = ""
    region_code: str = ""

    unresolved_reason: str = ""
    raw_record_ref: str = ""
    notes: list[str] = field(default_factory=list)

    def log(self, rung, registry, query, hit, detail="") -> None:
        self.attempts.append(Attempt(rung, registry, query, hit, detail))

    def note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)

    @property
    def resolved(self) -> bool:
        return self.tier is not Tier.P4

    @property
    def is_envelope(self) -> bool:
        """A holder's whole tenure in a district, not a specific harvest.

        R7 resolves by client number rather than by mark, so what comes back
        is everything that holder cut in that district - a superset of what
        the client actually bought. Coarser than the tier alone implies, and
        it must not be presented as a plot.
        """
        return self.matched_rung == "R7"

    @property
    def traceability(self) -> str:
        """How this result was reached. See Tier.traceability.

        An envelope is demoted a step regardless of its tier: a holder's whole
        tenure was reached through the company, not through the delivery.
        """
        base = self.tier.traceability
        if self.is_envelope and base == "direct":
            return "indirect"
        return base

    @property
    def area_ha(self) -> float:
        total = 0.0
        for f in self.features:
            v = (f.get("properties") or {}).get("FEATURE_AREA") or 0
            try:
                total += float(v) / 10000.0
            except (TypeError, ValueError):
                pass
        return round(total, 1)

    def row(self) -> dict[str, Any]:
        """Flat record for the resolution manifest."""
        return {
            "source_id": self.source_id,
            "identifier": self.identifier,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "jurisdiction": self.jurisdiction,
            "product_type": self.product_type,
            "class": self.klass.value if self.klass else "",
            "path": self.path,
            "precision_tier": self.tier.value,
            "tier_label": self.tier.label,
            "traceability": self.tier.traceability,
            "is_envelope": self.is_envelope,
            "shapes": ", ".join(s.value for s in self.shapes),
            "matched_rung": self.matched_rung,
            "matched_field": self.matched_field,
            "registry": self.registry,
            "blocks": len(self.features),
            "area_ha": self.area_ha,
            "verdict": self.verdict,
            "verdict_basis": self.verdict_basis,
            "land_type": self.land_type,
            "tenure_holder": self.tenure_holder,
            "client_number": self.client_number,
            "client_location": self.client_location,
            "district_code": self.district_code,
            "district_name": self.district_name,
            "region_code": self.region_code,
            "rungs_attempted": " > ".join(a.rung for a in self.attempts),
            "unresolved_reason": self.unresolved_reason,
            "raw_record_ref": self.raw_record_ref,
            "notes": "; ".join(self.notes),
            "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "licence": ATTRIBUTION,
        }

    def full(self) -> dict[str, Any]:
        d = self.row()
        d["attempts"] = [asdict(a) for a in self.attempts]
        return d
