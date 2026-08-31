#!/usr/bin/env python3
"""Unpack a DMP harvest-units export.

Harmac Pacific's own Digital Material Passports carry a
HarvestUnitsDownloadURL. The BC one returns three features - WestCoast,
SouthCoast, Interior - and fifteen megabytes. A region boundary is about fifty
kilobytes, so the size is the finding: each feature is a GeometryCollection
holding hundreds or thousands of individual harvest polygons, grouped up and
labelled only by region.

This pulls them apart and says what is actually in there.

    python tools/dmp_harvest_units.py harvest-units.geojson
    python tools/dmp_harvest_units.py harvest-units.geojson --explode out/
    python tools/dmp_harvest_units.py hu.geojson --compare areas-*.geojson

WHY IT MATTERS
--------------
There is no join key. No timber mark, no source id, no supplier - so this
cannot attribute a harvest to one of Harmac's supply sources. What it can do:

  - say how many harvest units actually underlie the declaration, and how big
    they are. A region-labelled file that turns out to hold 2,000 polygons of
    30 ha each is harvest-unit data that has been aggregated, not a boundary.

  - cross-check what we have resolved. If our FTEN cut blocks fall inside
    these, both are right about the same wood.

  - raise the question worth asking: where did these polygons come from?
    Someone had harvest-unit geometry for BC chip supply in 2025. If that
    source still exists it is worth more than any catchment we could draw.

ON THE GEOMETRY TYPE
--------------------
GeometryCollection is not permitted under EUDR - eudr_geojson rejects it.
So Harmac's filed declaration would fail the same check we would run on our
own output. That is their existing position rather than a problem with the
file, but it should be said out loud rather than discovered at audit.

Requires: shapely (optional - without it, counts only, no areas)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

try:
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ──────────────────────── passports and downloads ──────────────────────────

def is_dmp(blob: dict) -> bool:
    return isinstance(blob, dict) and "DigitalMaterialPassport" in blob


def dmp_info(path: str) -> dict | None:
    """The bits of a passport that matter here: what it declares, and where
    the geometry behind it lives."""
    try:
        blob = load(path)
    except Exception:
        return None
    if not is_dmp(blob):
        return None
    d = blob["DigitalMaterialPassport"]
    g = d.get("GeneralInformation") or {}
    refs = d.get("DMPReferences") or []
    return {
        "path": path,
        "id": g.get("UserDefinedId") or os.path.basename(path),
        "country": g.get("Country", ""),
        "state": g.get("State", ""),
        "units": [r.get("UserDefinedId") for r in refs],
        "url": d.get("HarvestUnitsDownloadURL") or "",
    }


def find_dmps(target: str) -> list[dict]:
    """Every passport at or under a path. A single file works too."""
    if os.path.isfile(target):
        info = dmp_info(target)
        return [info] if info else []
    out = []
    for name in sorted(os.listdir(target)):
        if not name.lower().endswith(".json"):
            continue
        info = dmp_info(os.path.join(target, name))
        if info:
            out.append(info)
    return out


def download(url: str, dest: str, log=print) -> str | None:
    """Fetch the harvest units, once. Cached on disk by design.

    S1Seven retains DMP data until delivery plus roughly a week, so a link
    that works today may not next month. A file already downloaded is not
    fetched again, and is not discarded.
    """
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log("    cached: {} ({:.1f} MB)".format(
            os.path.basename(dest), os.path.getsize(dest) / 1024 / 1024))
        return dest
    try:
        import requests
    except ImportError:
        log("    requests is not installed - cannot download")
        return None
    try:
        log("    downloading…")
        r = requests.get(url, timeout=600, stream=True)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    except Exception as exc:
        log("    download failed: {}".format(str(exc)[:110]))
        if os.path.exists(dest):
            os.remove(dest)
        return None
    log("    {:.1f} MB".format(os.path.getsize(dest) / 1024 / 1024))
    return dest


def walk(geom: dict):
    """Every single-part geometry inside, however deeply nested.

    Collections are opened and multiparts are exploded, so what comes out is
    always one Polygon, LineString or Point. Two reasons.

    First, EUDR does not accept a GeometryCollection at all, and a MultiPolygon
    standing for several separate cutblocks asserts they are one plot.

    Second, this file mixes two kinds of thing - small cutblocks and large
    regional areas - and they need different treatment downstream. You cannot
    sort them while they are bundled together inside one multipart.
    """
    if not geom:
        return
    t = geom.get("type")
    if t == "GeometryCollection":
        for g in geom.get("geometries", []):
            yield from walk(g)
    elif t == "MultiPolygon":
        for coords in geom.get("coordinates", []):
            yield {"type": "Polygon", "coordinates": coords}
    elif t == "MultiLineString":
        for coords in geom.get("coordinates", []):
            yield {"type": "LineString", "coordinates": coords}
    elif t == "MultiPoint":
        for coords in geom.get("coordinates", []):
            yield {"type": "Point", "coordinates": coords}
    else:
        yield geom


# ─────────────────────── cutblock or catchment ─────────────────────────────
#
# The download holds both: individual cutblocks, and much larger regional areas
# standing in for wood whose exact origin was not established. They need
# different handling - a cutblock is already a harvest area, a regional polygon
# is a search area that has to go through change detection before it means
# anything.
#
# Area is the signal. A BC coastal cutblock runs from under a hectare to a
# couple of hundred; the largest in Harmac's resolved set is 142 ha. Anything
# in the thousands is not a cutblock.
#
# The threshold is a parameter rather than a constant because it is a judgement
# about this data, not a fact about the world. Run the distribution first.

CUTBLOCK_MAX_HA = 1000.0


def classify(area: float, threshold: float = CUTBLOCK_MAX_HA) -> str:
    if area <= 0:
        return "unknown"
    return "cutblock" if area <= threshold else "catchment"


def distribution(areas: list[float]) -> str:
    """Where the sizes actually sit, so a threshold can be chosen rather than
    assumed."""
    if not areas:
        return "no areas - shapely and pyproj are needed"
    bands = [(0, 1), (1, 10), (10, 50), (50, 200), (200, 1000),
             (1000, 10000), (10000, 100000), (100000, float("inf"))]
    lines = []
    for lo, hi in bands:
        n = sum(1 for a in areas if lo < a <= hi)
        if not n:
            continue
        label = ">{:,.0f}".format(lo) if hi == float("inf") else \
            "{:,.0f} - {:,.0f}".format(lo, hi)
        bar = "#" * min(40, max(1, round(n / len(areas) * 40)))
        lines.append("    {:>18} ha  {:>6}  {}".format(label, n, bar))
    return "\n".join(lines)


def area_ha(geom: dict) -> float:
    """Geodesic area in hectares. Needs shapely and pyproj."""
    if not HAVE_SHAPELY:
        return 0.0
    try:
        from pyproj import Geod
        g = Geod(ellps="WGS84")
        s = shape(geom)
        if s.geom_type == "Polygon":
            polys = [s]
        elif s.geom_type == "MultiPolygon":
            polys = list(s.geoms)
        else:
            return 0.0
        total = 0.0
        for p in polys:
            a, _ = g.geometry_area_perimeter(p)
            total += abs(a)
        return total / 10000.0
    except Exception:
        return 0.0


def describe(path: str, explode_to: str | None = None,
             threshold: float = CUTBLOCK_MAX_HA,
             collected: dict | None = None, source_label: str = "") -> list[dict]:
    gj = load(path)
    feats = gj.get("features") or []
    size = os.path.getsize(path) / 1024 / 1024

    print("file      : {}  ({:.1f} MB)".format(os.path.basename(path), size))
    print("features  : {}".format(len(feats)))
    print("crs       : {}".format("present - EUDR forbids it"
                                  if "crs" in gj else "absent, correct"))
    if not HAVE_SHAPELY:
        print("\nshapely not installed - counts only, no areas")
        print("  pip install shapely pyproj\n")

    # Two files out, whatever came in: every cutblock together, every regional
    # polygon together. The declared unit and the passport it came from ride
    # along as properties, so nothing is lost by merging them.
    own = collected is None
    if own:
        collected = {"cutblock": [], "catchment": [], "unknown": []}

    out = []
    for i, f in enumerate(feats):
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        parts = list(walk(geom))
        types = Counter(p.get("type") for p in parts)
        areas = [area_ha(p) for p in parts] if HAVE_SHAPELY else []
        areas = [a for a in areas if a > 0]

        name = (props.get("UserDefinedId") or props.get("name")
                or props.get("id") or "feature {}".format(i))
        print("\n" + "-" * 66)
        print("{}".format(name))
        print("  outer type      : {}".format(geom.get("type")))
        print("  parts inside    : {}".format(len(parts)))
        print("  part types      : {}".format(dict(types)))
        if props:
            keys = [k for k in props if k not in ("UserDefinedId", "name")]
            print("  other properties: {}".format(", ".join(keys[:12]) or "none"))
        if areas:
            areas.sort()
            print("  total area      : {:,.0f} ha".format(sum(areas)))
            print("  median part     : {:,.1f} ha".format(areas[len(areas) // 2]))
            print("  smallest        : {:,.2f} ha".format(areas[0]))
            print("  largest         : {:,.0f} ha".format(areas[-1]))
            # A part median in the tens of hectares means these are harvest
            # units. A single part of a million hectares means it is a region
            # boundary and there is nothing underneath.
            med = areas[len(areas) // 2]
            if len(areas) > 20 and med < 2000:
                print("  reading         : harvest units, aggregated by region")
            elif len(areas) <= 3 and med > 100000:
                print("  reading         : a region boundary, no detail inside")
            else:
                print("  reading         : mixed - open it and look")

        out.append({"name": name, "parts": len(parts),
                    "area_ha": round(sum(areas), 1) if areas else None,
                    "cutblocks": sum(1 for a in areas if a <= threshold),
                    "catchments": sum(1 for a in areas if a > threshold),
                    "types": dict(types), "properties": props})

        if areas:
            print("  size spread:")
            print(distribution(areas))
            n_cut = sum(1 for a in areas if a <= threshold)
            print("  at {:,.0f} ha       : {} cutblock, {} catchment".format(
                threshold, n_cut, len(areas) - n_cut))

        if explode_to is not None:
            for n, p in enumerate(parts):
                a = area_ha(p)
                kind = classify(a, threshold)
                collected[kind].append({
                    "type": "Feature", "geometry": p,
                    "properties": {**props, "declared_unit": name,
                                   "declared_by": source_label,
                                   "part_index": n,
                                   "part_area_ha": round(a, 2),
                                   "harvest_area_kind": kind,
                                   "kind_basis": ("area {:,.1f} ha against a "
                                                  "{:,.0f} ha threshold".format(
                                                      a, threshold))}})

    if own and explode_to is not None:
        write_collections(collected, explode_to)

    return out


def write_collections(collected: dict, outdir: str) -> list[str]:
    """One file per kind, however many inputs went in."""
    os.makedirs(outdir, exist_ok=True)
    written = []
    print("\n" + "-" * 66)
    for kind in ("cutblock", "catchment", "unknown"):
        fs = collected.get(kind) or []
        if not fs:
            continue
        dest = os.path.join(outdir, "{}s.geojson".format(kind))
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection",
                       "name": "dmp_harvest_{}s".format(kind),
                       "features": fs}, fh)
        total = sum(f["properties"].get("part_area_ha", 0) for f in fs)
        units = {f["properties"].get("declared_by") for f in fs}
        print("{:<26} {:>6} features  {:>13,.0f} ha  from {} passport(s)".format(
            os.path.basename(dest), len(fs), total, len(units - {""}) or 1))
        written.append(dest)
    return written


def compare(hu_path: str, ours_path: str, our_features: list | None = None
            ) -> dict:
    """How much of what we resolved falls inside what they declared.

    Reported per tier, because a single percentage is not readable. A P2
    operating envelope is a holder's whole tenure in a district - thousands of
    blocks, most of which were never sold to this client - so it dominates any
    overall figure and tells you nothing. P1 is the number that matters: those
    are specific harvests matched to a specific mark.

    Three ordinary reasons for a low overlap, worth ruling out before reading
    anything into it:

      the periods differ. Ours carries every block ever recorded under a mark;
      a passport covers a stated window.

      the commodity differs. A chip passport declares chip intake. Log
      purchases resolved from timber marks are a different supply stream, even
      where the logs end up chipped.

      the envelopes swamp it. Filter to P1 before drawing a conclusion.
    """
    if not HAVE_SHAPELY:
        sys.exit("--compare needs shapely:  pip install shapely pyproj")

    theirs = []
    for f in load(hu_path).get("features", []):
        for p in walk(f.get("geometry") or {}):
            try:
                theirs.append(shape(p))
            except Exception:
                pass
    if not theirs:
        sys.exit("no usable geometry in the harvest units file")
    print("\ntheir harvest units : {} parts".format(len(theirs)))
    merged = unary_union(theirs)

    ours = our_features if our_features is not None else \
        load(ours_path).get("features", [])

    from collections import defaultdict
    stats = defaultdict(lambda: {"in": 0, "out": 0, "in_ids": set(),
                                 "out_ids": set()})
    skipped = 0
    for f in ours:
        g = f.get("geometry")
        if not g:
            skipped += 1
            continue
        try:
            geom = shape(g)
        except Exception:
            skipped += 1
            continue
        p = f.get("properties") or {}
        tier = p.get("harp_tier") or "?"
        ident = p.get("harp_identifier") or p.get("TIMBER_MARK") or "?"
        if merged.intersects(geom):
            stats[tier]["in"] += 1
            stats[tier]["in_ids"].add(ident)
        else:
            stats[tier]["out"] += 1
            stats[tier]["out_ids"].add(ident)

    total_in = sum(v["in"] for v in stats.values())
    total = total_in + sum(v["out"] for v in stats.values())
    print("\nour features        : {}".format(total))
    if skipped:
        print("  no geometry       : {}".format(skipped))
    print("\n{:<6}{:>10}{:>10}{:>9}   {}".format(
        "tier", "inside", "outside", "overlap", "identifiers in / out"))
    print("-" * 66)
    for tier in sorted(stats):
        v = stats[tier]
        n = v["in"] + v["out"]
        print("{:<6}{:>10}{:>10}{:>8.0f}%   {} / {}".format(
            tier, v["in"], v["out"], (v["in"] / n * 100) if n else 0,
            len(v["in_ids"]), len(v["out_ids"])))
    print("-" * 66)
    print("{:<6}{:>10}{:>10}{:>8.0f}%".format(
        "all", total_in, total - total_in,
        (total_in / total * 100) if total else 0))

    p1 = stats.get("P1a", 0) + stats.get("P1b", 0)
    if p1 and (p1["in"] + p1["out"]):
        pct = p1["in"] / (p1["in"] + p1["out"]) * 100
        print("\nP1 is the number that matters: {:.0f}% of blocks matched to a "
              "specific mark fall inside what they declared.".format(pct))
        if p1["out_ids"]:
            print("marks with no overlap at all: {}{}".format(
                ", ".join(sorted(p1["out_ids"])[:18]),
                " and {} more".format(len(p1["out_ids"]) - 18)
                if len(p1["out_ids"]) > 18 else ""))
    print("\nBefore reading anything into a low figure: the periods may "
          "differ, ours carries every block ever recorded under a mark, and a "
          "chip passport declares a different supply stream from a log "
          "purchase.")
    return {k: {"in": v["in"], "out": v["out"]} for k, v in stats.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("harvest_units",
                    help="a folder of passports, one passport, or an already "
                         "downloaded harvest units geojson")
    ap.add_argument("--explode", metavar="DIR",
                    help="write cutblocks and catchments to separate files, "
                         "one feature per polygon")
    ap.add_argument("--threshold", type=float, default=CUTBLOCK_MAX_HA,
                    help="hectares above which a polygon is treated as a "
                         "catchment rather than a cutblock (default 1000)")
    ap.add_argument("--compare", metavar="GEOJSON",
                    help="a HARP areas file, to check overlap")
    ap.add_argument("--json", metavar="PATH", help="write the summary as json")
    ap.add_argument("--cache", metavar="DIR", default="dmp_downloads",
                    help="where downloaded harvest units are kept")
    args = ap.parse_args()

    collected: dict[str, list] = {"cutblock": [], "catchment": [], "unknown": []}
    summary: list[dict] = []

    dmps = find_dmps(args.harvest_units)
    if dmps:
        print("{} passport(s) found\n".format(len(dmps)))
        for info in dmps:
            print("=" * 66)
            print("{}   {} {}   {} declared unit(s)".format(
                info["id"], info["country"], info["state"], len(info["units"])))
            if not info["url"]:
                print("  no HarvestUnitsDownloadURL - nothing to fetch")
                continue
            dest = os.path.join(args.cache, "{}.geojson".format(
                "".join(c if c.isalnum() or c in "-_" else "_"
                        for c in info["id"])))
            got = download(info["url"], dest)
            if not got:
                continue
            summary += describe(got, args.explode, args.threshold,
                                collected=collected, source_label=info["id"])
    else:
        summary = describe(args.harvest_units, args.explode, args.threshold,
                           collected=collected,
                           source_label=os.path.basename(args.harvest_units))

    if args.explode:
        write_collections(collected, args.explode)
    if args.compare:
        compare(args.harvest_units, args.compare)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
        print("\nsummary written to {}".format(args.json))

    print("\n" + "=" * 66)
    total = sum(s["parts"] for s in summary)
    cut = sum(s.get("cutblocks", 0) for s in summary)
    cat = sum(s.get("catchments", 0) for s in summary)
    print("{} polygons across {} declared units.".format(total, len(summary)))
    if cut or cat:
        print("  {} look like cutblocks - already harvest areas".format(cut))
        print("  {} look like regional areas - these are search areas and need"
              .format(cat))
        print("  change detection run inside them before they mean anything.")
    print("There is no timber mark, source id or supplier in this file, so it")
    print("cannot attribute a harvest to one of Harmac's supply sources. What")
    print("it can do is tell us how much detail sits under the declaration -")
    print("and prompt the question of where that detail came from.")


if __name__ == "__main__":
    main()
