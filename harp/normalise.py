"""Normalisation to sce_base.

All five acquisition paths converge here and must emit rows that are
indistinguishable in structure. This is the single most important module in
HARP: if the paths diverge, TraceMark sees five datasets rather than one.

sce_base is TraceMark's master entity table - one row per real-world thing,
with geometry attached. The schema below is INFERRED from three writers in
tracemark-eo, because no DDL exists in that repo:

    pointtopoly/billerud_prod_20251029.py    sce_base_selectors
    pointtopoly/pointtopoly.py               qweb_base_prep()
    domtar/logical/generate-bc-coastal-harvest

Before designing anything further against this, run INFORMATION_SCHEMA.COLUMNS
against a real sce_base and replace the guesswork.

TWO OPEN QUESTIONS, both for the TraceMark team, both five-minute answers:
    - Do we write to sce_base directly, or a staging table they promote?
    - Who registers sce_type values in db_primary_sources? Unregistered types
      cause assessments to silently skip every row.
"""

from __future__ import annotations

from typing import Any

from .resolution import Resolution, Tier

# A resolution's precision tier decides its eudr_sub_type, and whether it
# emits a row at all.
#
# THE RULE: a tier that exists to be searched does not emit a declared row.
#
# P1b, P2a and P3a are inputs to change detection - the parcel a mark was
# scaled from, the tenure a company holds, the district a mill sits in. None
# of them is where the wood was cut. Giving them a sub_type would say the
# opposite of the principle the pipeline is built on, so they emit nothing,
# the same as an unresolved source. They remain in the working files, which is
# where you go to see what was searched.
#
# What reaches a declaration is P1a - the harvest itself - or a detection.
#
# ON THE DETECTED TIERS. All three map to `catchment_polygon`, because the
# sub_type describes where a polygon came from rather than how good it is.
# Every one of them is ours, derived from imagery, whatever the strength of
# the identifier that led us to the area. The tier and the traceability value
# carry the difference between them; the sub_type should not try to.
TIER_TO_SUB_TYPE = {
    # The harvest block itself, from a public forest register.
    Tier.P1A: "database_polygon",
    # Detected geometry. The polygon is ours; the mark that led to it came off
    # the client's own delivery record, which is what keeps it at P1.
    Tier.P1C: "catchment_polygon",
    Tier.P2B: "catchment_polygon",
    Tier.P3B: "catchment_polygon",
    # Searched, not declared.
    Tier.P1B: None,
    Tier.P2A: None,
    Tier.P3A: None,
    Tier.P4: None,
}

# Named so the reason a row is absent can be reported rather than guessed at.
SEARCH_TIERS = {Tier.P1B, Tier.P2A, Tier.P3A}

# Inferred, not authoritative. See module docstring.
SCE_BASE_FIELDS = [
    "sce_id", "name", "sce_type", "eudr_sub_type", "sce_source", "commodity",
    "geom", "country", "province", "district",
    "batch_ID", "DestinationMill", "date_added", "modified_by",
]

# EUDR Article 9. Encoded once, here, so nobody re-derives it.
MIN_AREA_HA = 1.0        # below this, discard
POINT_AREA_HA = 4.0      # below this, a centroid point is acceptable


def size_class(area_ha: float) -> str:
    """'discard' | 'point' | 'polygon'."""
    if area_ha < MIN_AREA_HA:
        return "discard"
    if area_ha < POINT_AREA_HA:
        return "point"
    return "polygon"


def from_ften(feature: dict, source: str, sce_type: str = "CutBlock",
              keep_source: bool = False) -> dict:
    """One FTEN cutblock to one sce_base row.

    ON THE RAW RECORD
    -----------------
    When a polygon is challenged in year three you want the record as pulled,
    not just the fields we happened to map. That record is still kept - but as
    a sidecar keyed on sce_id (see `provenance_rows`), not copied into every
    payload row. Carrying it inline added roughly a third again to a file that
    goes to TraceMark, for data TraceMark does not read.

    `keep_source=True` restores the old inline behaviour for a caller that
    wants a single self-contained artefact.
    """
    p = feature.get("properties", {}) or {}
    mark = p.get("TIMBER_MARK")
    skey = p.get("CUT_BLOCK_SKEY")

    row = {
        "sce_id": f"FTEN-{mark}-{skey}" if mark and skey else f"FTEN-{skey}",
        "name": p.get("CUT_BLOCK_ID") or mark,
        "sce_type": sce_type,
        "eudr_sub_type": "database_polygon",
        "sce_source": source,
        "commodity": "Wood",
        "geom": feature.get("geometry"),
        "country": "Canada",
        "province": "British Columbia",
        "district": p.get("GEOGRAPHIC_DISTRICT_NAME") or p.get("ADMIN_DISTRICT_NAME"),
        "modified_by": "harp-ften",
    }
    if keep_source:
        row["_source"] = p
    return row


def provenance_rows(res: Resolution) -> list[dict]:
    """The record as pulled, keyed on sce_id.

    Written alongside the payload rather than inside it. Same audit value,
    none of the weight where it is not read.
    """
    out = []
    for feat in res.features:
        p = feat.get("properties", {}) or {}
        mark, skey = p.get("TIMBER_MARK"), p.get("CUT_BLOCK_SKEY")
        out.append({
            "sce_id": (f"FTEN-{mark}-{skey}" if mark and skey
                       else f"FTEN-{skey}"),
            "source_id": res.source_id,
            "identifier": res.identifier,
            "precision_tier": res.tier.value,
            "registry": res.registry,
            "matched_rung": res.matched_rung,
            "retrieved_at": None,
            "raw": p,
        })
    return out


def from_resolution(res: Resolution, sce_type: str = "CutBlock",
                    keep_source: bool = False,
                    dissolve_envelopes: bool = True) -> list[dict]:
    """Every feature of one resolution as sce_base rows.

    The precision tier travels onto the row. A P4 result is an operating area
    and is never directly traced, so it is carried explicitly rather than
    inferred from the fact that geometry exists.

    ON ENVELOPES
    ------------
    A P2 result from R7 is a holder's whole tenure in a district - one answer
    about one source, which happens to be drawn as many polygons. Emitting one
    row per polygon says the opposite: a single chip supplier became 2,283
    plots, which is both wrong and most of the file.

    So a P2 envelope is dissolved to a single row carrying a MultiPolygon and
    the block count. The individual blocks are not lost - they remain in the
    GeoJSON, which is where you go to see them. Requires shapely; without it
    the rows are emitted individually and a note says so.

    Set dissolve_envelopes=False to keep one row per polygon.
    """
    sub_type = TIER_TO_SUB_TYPE.get(res.tier)
    if sub_type is None:
        # Either nothing resolved, or what resolved is a place to look rather
        # than a harvest. Both are absent from a declaration; only one is a
        # gap, and the caller can tell them apart with SEARCH_TIERS.
        return []

    if (dissolve_envelopes and res.matched_rung == "R7"
            and len(res.features) > 1):
        row = _dissolved_row(res, sce_type, sub_type)
        if row is not None:
            return [row]

    rows = []
    for feat in res.features:
        row = from_ften(feat, source=res.registry or "FTEN", sce_type=sce_type,
                        keep_source=keep_source)
        row["eudr_sub_type"] = sub_type
        row["_harp"] = {
            "source_id": res.source_id,
            "identifier": res.identifier,
            "supplier_name": res.supplier_name,
            "precision_tier": res.tier.value,
            "traceability": res.tier.traceability,
            "class": res.klass.value if res.klass else "",
            "path": res.path,
            "matched_rung": res.matched_rung,
            "matched_field": res.matched_field,
            "land_type": res.land_type,
            "tenure_holder": res.tenure_holder,
            "client_number": res.client_number,
            "verdict": res.verdict,
            "verdict_basis": res.verdict_basis,
        }
        rows.append(row)
    return rows


def _dissolved_row(res: Resolution, sce_type: str, sub_type: str) -> dict | None:
    """One row for a whole operating envelope. None if shapely is absent."""
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
    except ImportError:
        return None
    try:
        geoms = [shape(f["geometry"]) for f in res.features
                 if f.get("geometry")]
        if not geoms:
            return None
        merged = unary_union(geoms)
    except Exception:
        return None

    return {
        "sce_id": "HARP-ENV-{}-{}".format(res.client_number or "x",
                                          res.district_code or "x"),
        "name": "{} tenure in {}".format(
            res.tenure_holder or res.identifier,
            res.district_name or res.district_code or "district"),
        "sce_type": sce_type,
        "eudr_sub_type": sub_type,
        "sce_source": res.registry or "FTEN",
        "commodity": "Wood",
        "geom": mapping(merged),
        "country": "Canada",
        "province": "British Columbia",
        "district": res.district_name or res.district_code,
        "modified_by": "harp-ften",
        "_harp": {
            "source_id": res.source_id,
            "identifier": res.identifier,
            "supplier_name": res.supplier_name,
            "precision_tier": res.tier.value,
            "traceability": res.traceability,
            "is_envelope": res.is_envelope,
            "class": res.klass.value if res.klass else "",
            "path": res.path,
            "matched_rung": res.matched_rung,
            "land_type": res.land_type,
            "tenure_holder": res.tenure_holder,
            "client_number": res.client_number,
            "verdict": res.verdict,
            "dissolved_from_blocks": len(res.features),
            "note": ("Operating envelope - the holder's tenure in this "
                     "district, dissolved to one geometry. A superset of what "
                     "was bought, not a plot. Individual blocks are in the "
                     "GeoJSON."),
        },
    }


def why_no_row(res: Resolution) -> str:
    """Why this resolution produced no sce_base row.

    Two very different absences share one outcome and should not be reported
    as one number. A search area is working as intended and is waiting on
    detection; an unresolved source is a question for the client.
    """
    if TIER_TO_SUB_TYPE.get(res.tier) is not None:
        return ""
    if res.tier in SEARCH_TIERS:
        return ("a place to look, not a harvest - awaiting detection")
    return "nothing resolved - a client question"


def from_supplier_geodata(feature: dict, source: str, sce_type: str = "CutBlock") -> dict:
    raise NotImplementedError("Pending a real supplier file to design against.")


def from_detection(feature: dict, source: str, sce_type: str = "CutBlock") -> dict:
    raise NotImplementedError("Pending the tracemark-eo integration decision.")


def check(row: dict) -> list[str]:
    """Problems with a normalised row. Empty list means it is fit to load."""
    problems = []
    if not row.get("sce_id"):
        problems.append("missing sce_id")
    if not row.get("geom"):
        problems.append("missing geometry")
    if not row.get("sce_type"):
        problems.append("missing sce_type")
    if not row.get("eudr_sub_type"):
        problems.append("missing eudr_sub_type - provenance is not optional")
    return problems
