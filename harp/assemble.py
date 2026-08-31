"""Stage 3a - assembling one collection from many resolutions.

Every class of source produces geometry by a different route: a BC timber mark
through the public register, a private mark through a supplier's own file or a
catchment, a US source through satellite detection. They arrive as separate
resolutions and have to become one FeatureCollection before anything downstream
can look at them.

Three things happen here, and none of them are cosmetic.

DEDUPLICATION
    Twelve of Harmac's identifiers serve more than one source, so the same cut
    block arrives several times. Left alone it double-counts area and inflates
    a validation run. Features are keyed on their registry identity where they
    have one, and the duplicate carries a note naming the other sources it also
    belongs to rather than being silently dropped.

TIER RECONCILIATION
    A pooled commodity legitimately draws on sources at different precisions.
    The mix must be reported, never averaged: a collection containing one P4
    feature is not a P1 collection. `summary()` returns the distribution and
    the worst tier present.

THE CRS MEMBER
    A GeoJSON `crs` member is forbidden under EUDR - eudr_geojson raises 1.1.4
    for it - but the ArcGIS service returns one and our own writer adds one.
    It is stripped here rather than at validation time, because a collection
    that would fail on its own container should never be handed on in the first
    place.

Provenance survives assembly. Every feature keeps `harp_source_id`, which is
what lets a finding be traced back after cleaning has renumbered everything.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from .resolution import ATTRIBUTION, Resolution, Tier

# Registry fields that identify a feature uniquely enough to spot a duplicate.
# Checked in order; the first present wins.
IDENTITY_FIELDS = (
    "CUT_BLOCK_SKEY", "OBJECTID",
    ("TIMBER_MARK", "CUT_BLOCK_ID"),
    ("HARVEST_AUTH_FOREST_FILE_ID", "CUT_BLOCK_ID"),
)


def _identity(props: dict) -> str | None:
    for field in IDENTITY_FIELDS:
        if isinstance(field, tuple):
            vals = [str(props.get(f) or "").strip() for f in field]
            if all(vals):
                return "|".join(vals)
        else:
            v = props.get(field)
            if v not in (None, ""):
                return "{}={}".format(field, v)
    return None


def _tag(res: Resolution, props: dict | None) -> dict:
    out = dict(props or {})
    out.update({
        "harp_source_id": res.source_id,
        "harp_identifier": res.identifier,
        # The code is the reliable identity; a name is not always present.
        "harp_supplier": res.supplier_name or res.supplier_id,
        "harp_supplier_code": res.supplier_id,
        "harp_class": res.klass.value if res.klass else "",
        "harp_path": res.path,
        "harp_tier": res.tier.value,
        "harp_tier_label": res.tier.label,
        "harp_traceability": res.traceability,
        "harp_is_envelope": res.is_envelope,
        "harp_registry": res.registry,
        "harp_matched_rung": res.matched_rung,
        "harp_tenure_holder": res.tenure_holder,
        "harp_land_type": res.land_type,
        "harp_district": res.district_name or res.district_code,
        "harp_jurisdiction": res.jurisdiction,
    })
    return out


def assemble(results: Iterable[Resolution], dedupe: bool = True
             ) -> tuple[dict, dict[str, Any]]:
    """One FeatureCollection from many resolutions.

    Returns (collection, report). The report says what was merged, what was
    deduplicated and which tiers are present - all of which a caller needs
    before deciding what the collection may be used for.
    """
    results = list(results)
    features: list[dict] = []
    seen: dict[str, int] = {}
    duplicates = 0

    for res in results:
        for f in res.features:
            props = _tag(res, f.get("properties"))
            ident = _identity(props)

            if dedupe and ident and ident in seen:
                duplicates += 1
                kept = features[seen[ident]]["properties"]
                also = kept.get("harp_also_source_ids", "")
                ids = [x for x in also.split(";") if x]
                if res.source_id not in ids:
                    ids.append(res.source_id)
                kept["harp_also_source_ids"] = ";".join(ids)
                # keep the better tier if a duplicate arrived by a better route
                if Tier(props["harp_tier"]).value < kept["harp_tier"]:
                    kept.update({k: v for k, v in props.items()
                                 if k.startswith("harp_")})
                continue

            if ident:
                seen[ident] = len(features)
            features.append({"type": "Feature",
                             "geometry": f.get("geometry"),
                             "properties": props})

    tiers = Counter(f["properties"]["harp_tier"] for f in features)
    src_tiers = Counter(r.tier.value for r in results)
    worst = max(tiers) if tiers else Tier.P4.value

    report = {
        "sources": len(results),
        "sources_with_geometry": sum(1 for r in results if r.features),
        "features": len(features),
        "duplicates_merged": duplicates,
        "feature_tiers": dict(sorted(tiers.items())),
        "source_tiers": dict(sorted(src_tiers.items())),
        "worst_tier_present": worst,
        "traceability": dict(sorted(Counter(
            f["properties"].get("harp_traceability", "none")
            for f in features).items())),
    }

    # No crs member. EUDR forbids it and eudr_geojson raises 1.1.4.
    collection = {
        "type": "FeatureCollection",
        "name": "harp_harvest_areas",
        "features": features,
    }
    return collection, report


def summary(report: dict) -> str:
    lines = ["{} features from {} sources".format(report["features"],
                                                  report["sources"])]
    if report["duplicates_merged"]:
        lines.append("  {} duplicate features merged".format(
            report["duplicates_merged"]))
    for tier, n in report["feature_tiers"].items():
        lines.append("  {:<4} {}".format(tier, n))
    trace = report.get("traceability") or {}
    if len(trace) > 1:
        lines.append("  traceability: " + ", ".join(
            "{} {}".format(n, k) for k, n in trace.items()))
        lines.append("  Mixed - some of this geometry was reached through the "
                     "supplier or by overlap rather than from the delivery. "
                     "Worst tier: " + report["worst_tier_present"])
    return "\n".join(lines)


def split_by_traceability(collection: dict) -> tuple[dict, dict]:
    """Separate directly traced geometry from the rest.

    A pooled commodity can legitimately mix methods, but the two subsets are
    used differently and splitting them is cheaper than explaining later why a
    district polygon sat in the same file as a cut block.

    Direct means a specific piece of land from an authoritative register, tied
    to the fibre by an identifier on the delivery. Everything else was reached
    through the supplier or by overlap.
    """
    direct, other = [], []
    for f in collection.get("features", []):
        (direct if f["properties"].get("harp_traceability") == "direct"
         else other).append(f)
    base = {k: v for k, v in collection.items() if k != "features"}
    return ({**base, "features": direct, "name": "harp_direct"},
            {**base, "features": other, "name": "harp_indirect"})


def stamp(collection: dict, extra: dict | None = None) -> dict:
    """Add run metadata. Kept out of `assemble` so the collection handed to
    validation is exactly what will be written."""
    out = dict(collection)
    out["metadata"] = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature_count": len(collection.get("features", [])),
        "licence": ATTRIBUTION,
        **(extra or {}),
    }
    return out
