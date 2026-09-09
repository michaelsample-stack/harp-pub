#!/usr/bin/env python3
"""Split the geodata into discrete blocks and broad areas.

    python tools/split_geometry.py --catchments catchment_out/catchments_*.geojson
    python tools/split_geometry.py --catchments ... --areas data/outbox/areas-*.geojson
    python tools/split_geometry.py --catchments ... --max-block-ha 2000

TWO FILES, ONE SCHEMA
---------------------
    blocks.geojson    discrete harvest-sized geometry - individual cut blocks
                      and titled parcels
    areas.geojson     broad regions - districts, counties, national forests,
                      and any operator envelope large enough to behave like one

Both carry identical fields. A consumer should be able to read either without
knowing which it opened, and tell them apart by `harp_geometry_kind`.

WHY SPLIT AT ALL
----------------
Not because change detection cannot handle a small polygon - it can. The
detection table holds harvest polygons already computed, and intersecting a
two-hectare block against it is no harder than intersecting a district.

The constraint is the pipeline around it, which runs one query per feature.
Five thousand blocks means five thousand queries and five thousand output
files. Broad areas are few and large, so they go through as they are; blocks
are many and small, and are better handled per supplier with one query against
the union of their blocks, then rejoined locally.

ONE POLYGON PER SUPPLIER, EVEN WHERE THEY OVERLAP
-------------------------------------------------
Two operators working the same district each get their own copy of that
district. The geometry is duplicated and that is correct: a catchment belongs
to a supplier, and merging them would lose which supplier a detection should
be attributed to.

SIZE IS CHECKED, NOT ASSUMED
----------------------------
An operator's tenure is meant to be individual cut blocks, but nothing
guarantees every record is one. Anything above the block threshold is moved to
the broad-areas file, and the distribution is reported so the threshold can be
argued with rather than trusted.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
from datetime import datetime

# A cut block above this is not behaving like a cut block. BC coastal blocks
# run from under a hectare to a couple of hundred; the largest in Harmac's
# resolved set is 142 ha. The threshold is deliberately generous.
MAX_BLOCK_HA = 2000.0

# Every field both files carry. Absent values are written as empty rather than
# omitted, so the two files have the same shape and a consumer never has to
# check whether a key exists.
SCHEMA = [
    "harp_supplier", "harp_supplier_code", "harp_jurisdiction",
    "harp_geometry_kind", "harp_method", "harp_source_system",
    "harp_key", "harp_key_name",
    "harp_timber_mark", "harp_district",
    "harp_area_ha",
    "harp_tier", "harp_is_envelope", "harp_plot_claimable",
    "harp_declared_by_supplier", "harp_also_in_register",
    "harp_basis", "harp_note", "harp_attribution",
]

# What each incoming method becomes
KIND = {
    "operator tenure": "cut_block",
    "named district": "district",
    "named county": "county",
    "national forest": "national_forest",
    "mill buffer": "mill_buffer",
}

BLOCK_KINDS = {"cut_block", "parcel"}


def area_ha(geom) -> float:
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        g = Geod(ellps="WGS84")
        s = shape(geom)
        if s.geom_type == "Polygon":
            polys = [s]
        elif s.geom_type == "MultiPolygon":
            polys = list(s.geoms)
        else:
            return 0.0
        return sum(abs(g.geometry_area_perimeter(p)[0]) for p in polys) / 10000.0
    except Exception:
        return 0.0


def norm(props: dict, defaults: dict) -> dict:
    """Every field in SCHEMA, taken from the feature or the defaults."""
    out = {}
    for k in SCHEMA:
        v = props.get(k)
        if v in (None, ""):
            v = defaults.get(k, "")
        out[k] = v
    return out


def from_catchments(path: str, max_block: float, log=print) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh)
    out = []
    for f in gj.get("features") or []:
        p = f.get("properties") or {}
        method = p.get("harp_method", "")
        kind = KIND.get(method, "unknown")
        # The catchment builder writes plain `area_ha` on tenure blocks and
        # nothing at all on districts. Take whichever is present, and fall
        # back to measuring the geometry - an area of zero would let an
        # oversized polygon through the size check unnoticed.
        a = 0.0
        for key in ("harp_area_ha", "area_ha", "part_area_ha",
                    "FEATURE_AREA_HA"):
            try:
                a = float(p.get(key) or 0)
            except (TypeError, ValueError):
                a = 0.0
            if a:
                break
        if not a:
            a = area_ha(f.get("geometry"))
        props = norm(p, {
            "harp_geometry_kind": kind,
            "harp_area_ha": round(a, 2),
            "harp_jurisdiction": p.get("district", "") or "",
            "harp_timber_mark": p.get("timber_mark", ""),
            "harp_district": p.get("district", ""),
            "harp_tier": p.get("harp_tier", ""),
            "harp_is_envelope": p.get("harp_is_envelope", True),
            "harp_plot_claimable": False,
        })
        out.append({"type": "Feature", "geometry": f.get("geometry"),
                    "properties": props})
    log("  {} feature(s) from the catchment layer".format(len(out)))
    return out


def from_areas(path: str, log=print) -> list[dict]:
    """HARP's own resolved harvest areas."""
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh)
    out = []
    for f in gj.get("features") or []:
        p = f.get("properties") or {}
        tier = p.get("harp_tier", "")
        # A parcel is discrete and real, but it is the titled land rather than
        # the cut - kept apart from cut_block so nobody reads it as one.
        kind = "parcel" if p.get("harp_route") == "R5b" or "parcel" in str(
            p.get("harp_registry", "")).lower() else "cut_block"
        if p.get("harp_is_envelope"):
            kind = "tenure_envelope"
        a = 0.0
        for key in ("harp_area_ha", "area_ha", "FEATURE_AREA_HA"):
            try:
                a = float(p.get(key) or 0)
            except (TypeError, ValueError):
                a = 0.0
            if a:
                break
        if not a:
            a = area_ha(f.get("geometry"))
        props = norm(p, {
            "harp_supplier": p.get("harp_supplier_name", ""),
            "harp_supplier_code": p.get("harp_source_id", ""),
            "harp_geometry_kind": kind,
            "harp_method": "timber mark" if kind == "cut_block"
                           else "private mark",
            "harp_source_system": p.get("harp_registry", ""),
            "harp_key": p.get("harp_identifier", ""),
            "harp_timber_mark": p.get("TIMBER_MARK", "")
                                or p.get("harp_identifier", ""),
            "harp_district": p.get("GEOGRAPHIC_DISTRICT_CODE", ""),
            "harp_area_ha": round(a, 2),
            "harp_tier": tier,
            "harp_is_envelope": bool(p.get("harp_is_envelope")),
            "harp_plot_claimable": bool(p.get("harp_plot_claimable")),
        })
        out.append({"type": "Feature", "geometry": f.get("geometry"),
                    "properties": props})
    log("  {} feature(s) from the resolved areas".format(len(out)))
    return out


def distribution(areas: list[float]) -> str:
    if not areas:
        return "    no areas available"
    bands = [(0, 1), (1, 10), (10, 50), (50, 200), (200, 500), (500, 2000),
             (2000, 10000), (10000, float("inf"))]
    lines = []
    for lo, hi in bands:
        n = sum(1 for a in areas if lo < a <= hi)
        if not n:
            continue
        label = ">{:,.0f}".format(lo) if hi == float("inf") else \
            "{:,.0f} - {:,.0f}".format(lo, hi)
        bar = "#" * min(44, max(1, round(n / len(areas) * 44)))
        lines.append("    {:>16} ha  {:>6}  {}".format(label, n, bar))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catchments", required=True,
                    help="the catchment geojson (glob ok)")
    ap.add_argument("--areas", help="a HARP areas geojson (glob ok)")
    ap.add_argument("--max-block-ha", type=float, default=MAX_BLOCK_HA,
                    help="above this, a block is treated as a broad area")
    ap.add_argument("--out", default="split_out")
    args = ap.parse_args()

    def one(pattern):
        hits = sorted(glob.glob(pattern))
        if not hits:
            raise SystemExit("no file matching {}".format(pattern))
        return hits[-1]

    print("Splitting into discrete blocks and broad areas\n")
    feats = from_catchments(one(args.catchments), args.max_block_ha)
    if args.areas:
        feats += from_areas(one(args.areas))

    # size check, before anything is sorted
    block_areas = [f["properties"]["harp_area_ha"] for f in feats
                   if f["properties"]["harp_geometry_kind"] in BLOCK_KINDS
                   and f["properties"]["harp_area_ha"]]
    print("\nsize of everything currently classed as a block or parcel:")
    print(distribution(block_areas))

    blocks, areas, moved = [], [], 0
    for f in feats:
        p = f["properties"]
        kind = p["harp_geometry_kind"]
        a = p["harp_area_ha"] or 0
        if kind in BLOCK_KINDS and a > args.max_block_ha:
            # Not behaving like a cut block. Moved rather than left to skew
            # the blocks file, and the reason is recorded on the feature.
            p["harp_geometry_kind"] = "oversized_block"
            p["harp_note"] = ("{:,.0f} ha - too large to behave as a cut "
                              "block, treated as a broad area. {}".format(
                                  a, p.get("harp_note", ""))).strip()
            areas.append(f)
            moved += 1
        elif kind in BLOCK_KINDS:
            blocks.append(f)
        else:
            areas.append(f)

    if moved:
        print("\n{} block(s) exceeded {:,.0f} ha and were moved to the broad "
              "areas file.".format(moved, args.max_block_ha))
    else:
        print("\nNo block exceeded {:,.0f} ha.".format(args.max_block_ha))

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    note = ("Search areas and harvest areas for Harmac Pacific fibre "
            "suppliers. Geometry is duplicated where two suppliers share an "
            "area - each supplier carries its own copy, so a detection can be "
            "attributed. Read harp_geometry_kind to tell them apart.")
    written = []
    for name, fs in (("blocks", blocks), ("areas", areas)):
        path = os.path.join(args.out, "{}_{}.geojson".format(name, stamp))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection",
                       "name": "harp_{}".format(name),
                       "metadata": {"generated": datetime.now().isoformat(
                                        timespec="seconds"),
                                    "features": len(fs),
                                    "schema": SCHEMA,
                                    "max_block_ha": args.max_block_ha,
                                    "note": note},
                       "features": fs}, fh)
        written.append((path, fs))

    print("\n" + "-" * 70)
    for path, fs in written:
        kinds = Counter(f["properties"]["harp_geometry_kind"] for f in fs)
        sup = {f["properties"]["harp_supplier"] for f in fs}
        tot = sum(f["properties"]["harp_area_ha"] or 0 for f in fs)
        print("\n{}".format(os.path.basename(path)))
        print("  {:,} feature(s), {} supplier(s), {:,.0f} ha".format(
            len(fs), len(sup - {""}), tot))
        for k, n in kinds.most_common():
            print("    {:<20}{:>7}".format(k, n))

    print("\n" + "-" * 70)
    print("Both files carry the same {} fields. A consumer can read either "
          "without knowing which.".format(len(SCHEMA)))
    print("Where two suppliers share an area, each has its own copy - the "
          "geometry repeats and the attribution does not.")


if __name__ == "__main__":
    main()
