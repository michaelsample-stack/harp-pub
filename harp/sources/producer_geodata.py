"""Harvest areas a producer declared, in their own files.

    from harp.sources import producer_geodata
    feats, report = producer_geodata.read(paths, month="2026-07")

WHAT THESE ARE
--------------
A supplier exports their own harvest areas as GeoJSON, one file per contract
and boom, each feature carrying a geometry, a source id, a supplier, and a
list of products with timber marks, volumes and production dates.

They are taken at their word. The producer is asserting they harvested here,
and that assertion is the evidence - which is what makes these P1d rather than
P1a. Nothing here is checked against a register.

WHY NOT CHECKED
---------------
It was tried. Of 63 distinct timber marks in one batch, 21 resolved in the BC
tenure register and only 6% of features produced a good geometric match. Not
because the data is bad: the two largest suppliers work private fee-simple land
on Vancouver Island - the old E&N Railway grant - which is outside Crown tenure
by definition. Their marks were never going to be there.

Where a mark does resolve the match is exact - 38.01 ha against 38.01 ha,
centroids 1.3 m apart. So the register is used for one thing only: finding a
better producer name than a placeholder.

FOUR THINGS THE READER FIXES OR RECORDS
---------------------------------------
**Longitude in 0-360.** Some records give Vancouver Island as 235.5 rather
than -124.5 - the same place counted eastward from Greenwich instead of west.
Normalised before anything touches the geometry, because everything downstream
produces confident nonsense otherwise.

**Duplication.** One batch held 1,450 features and 346 distinct ones. A block
feeding several booms is exported once per boom. Deduplicated on source id and
geometry, or area is overcounted four times over.

**Placeholder identity.** `"PURCHASE Name"` where the supplier will not name
their upstream. Never shipped as a producer name - see `producer_of`.

**Geometry that cannot be shipped.** Points with no boundary, and slivers under
a twentieth of a hectare. Kept and annotated rather than dropped, because a
feature quietly discarded is one nobody can ask about later.

THE MONTH A FEATURE BELONGS TO
------------------------------
Its production dates, not its harvest dates.

Harvest dates are 65% populated and one reads 2001-12-31. Production dates are
100% populated across every line item. They record when the wood ran at the
mill rather than when it was cut, which is a different thing - but it is the
right thing for assembling a month, because a month of harvest areas is a month
of what was used.

A block consumed over three months belongs to all three. That is not
duplication; it is the same ground feeding three months of production.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import date

# A producer's own name for itself, where the file gives a domain.
ORIGINATOR_NAMES = {
    "mosaicforests.com": "Mosaic Forest Management",
}

# Values that are not a name. `PURCHASE Name` is the literal string a supplier
# exports where they will not identify their upstream.
PLACEHOLDER_NAMES = {"PURCHASE NAME", "PURCHASE", "NAME", "SUPPLIER NAME",
                     "UNKNOWN", "N/A", ""}

# Below this a polygon is a sliver rather than a harvest area.
MIN_AREA_HA = 0.05


def looks_like_producer_geodata(obj) -> bool:
    """Is this one of these files?

    Recognised by shape rather than by the supplier's domain, so a second
    producer exporting the same structure works without a code change.
    """
    if not isinstance(obj, dict) or obj.get("type") != "FeatureCollection":
        return False
    for f in obj.get("features") or []:
        p = f.get("properties") or {}
        if "Originator" in p and "Products" in p:
            return True
    return False


# ─────────────────────────────── geometry ──────────────────────────────────

def normalise_longitude(geom: dict) -> tuple[dict, bool]:
    """Bring 0-360 longitudes back to -180..180.

    Returns the geometry and whether anything moved.
    """
    moved = [False]

    def fix(c):
        if c[0] > 180:
            moved[0] = True
            return [c[0] - 360] + list(c[1:])
        return list(c)

    def walk(coords, depth):
        if depth == 0:
            return fix(coords)
        return [walk(c, depth - 1) for c in coords]

    depth = {"Point": 0, "MultiPoint": 1, "LineString": 1, "Polygon": 2,
             "MultiPolygon": 3}.get(geom.get("type"))
    if depth is None:
        return geom, False
    out = {"type": geom["type"],
           "coordinates": walk(geom.get("coordinates") or [], depth)}
    return out, moved[0]


def area_ha(geom: dict) -> float:
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        s = shape(geom)
        if s.geom_type not in ("Polygon", "MultiPolygon"):
            return 0.0
        g = Geod(ellps="WGS84")
        polys = [s] if s.geom_type == "Polygon" else list(s.geoms)
        return sum(abs(g.geometry_area_perimeter(p)[0]) for p in polys) / 10000.0
    except Exception:
        return 0.0


def _repair(geom: dict) -> tuple[dict, bool]:
    """A self-intersection or a repeated vertex. buffer(0) is the usual fix."""
    try:
        from shapely.geometry import mapping, shape
        s = shape(geom)
        if s.is_valid:
            return geom, False
        fixed = s.buffer(0)
        if fixed.is_empty:
            return geom, False
        return mapping(fixed), True
    except Exception:
        return geom, False


# ─────────────────────────────── identity ──────────────────────────────────

def producer_of(props: dict, mark_lookup=None) -> tuple[str, str, str]:
    """Who cut this, and how confident we are of the answer.

    Three rungs. The first is the producer's own word. The second is used only
    where the first is a placeholder, and it is a lookup for a name - not a
    check on their geometry. The third names whoever declared the file, which
    is honest about being the declaring party rather than the harvester.

    Returns (name, source, note).
    """
    name = str((props.get("Supplier") or {}).get("Name") or "").strip()
    if name and name.upper() not in PLACEHOLDER_NAMES:
        return name, "producer declaration", ""

    # The supplier would not say. A Crown mark names its holder, and that is a
    # better answer than a placeholder even though most of these are private
    # land and will not resolve.
    marks = [str(p.get("Timbermark") or "").strip()
             for p in props.get("Products") or []]
    marks = [m for m in marks if m]
    if mark_lookup:
        for m in marks:
            holder = mark_lookup(m)
            if holder:
                return (holder, "forest register, via the declared timber mark",
                        'the producer was given as "{}"'.format(name or "blank"))

    origin = str(props.get("Originator") or "").strip()
    display = ORIGINATOR_NAMES.get(origin.lower(), origin)
    if display:
        return (display,
                "originator - the declaring party, not the harvester",
                'the producer was given as "{}"'.format(name or "blank"))
    return "", "", 'the producer was given as "{}"'.format(name or "blank")


# ──────────────────────────────── dates ────────────────────────────────────

def _months(props: dict) -> tuple[set, str, str]:
    """Every month this feature had production in.

    Production, not harvest. A block consumed over three months belongs to all
    three - it is the same ground feeding three months of output.
    """
    months, first, last = set(), "", ""
    for p in props.get("Products") or []:
        for key in ("ProductionFromDate", "ProductionToDate"):
            v = str(p.get(key) or "").strip()
            if len(v) >= 7 and v[4] == "-":
                months.add(v[:7])
                if not first or v < first:
                    first = v
                if not last or v > last:
                    last = v
    return months, first, last


def _date_note(props: dict) -> str:
    hs = str(props.get("HarvestStartDate") or "").strip()
    he = str(props.get("HarvestEndDate") or "").strip()
    if he == "2001-12-31" and not hs:
        return "the harvest end date is a placeholder"
    if hs and he and he < hs:
        return "the harvest dates run backwards: {} to {}".format(hs, he)
    return ""


# ──────────────────────────────── reading ──────────────────────────────────

def read(paths, month: str = "", mark_lookup=None, log=print) -> tuple:
    """Every distinct harvest area across these files.

    `month` as YYYY-MM keeps only features with production in it. `mark_lookup`
    is an optional callable taking a timber mark and returning a holder name,
    used only to replace a placeholder producer.
    """
    if isinstance(paths, str):
        paths = [paths]

    seen, feats = {}, []
    counts = Counter()
    files_read = 0

    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:
            log("  could not read {}: {}".format(os.path.basename(path), exc))
            continue
        if not looks_like_producer_geodata(doc):
            continue
        files_read += 1

        for f in doc.get("features") or []:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            if not geom:
                counts["no geometry"] += 1
                continue

            geom, moved = normalise_longitude(geom)
            if moved:
                counts["longitude normalised"] += 1

            source_id = str(props.get("SourceID") or "").strip()
            key = (source_id,
                   json.dumps(geom.get("coordinates"), sort_keys=True))
            if key in seen:
                # The same block, exported again for another boom. Its
                # production dates and volumes still count, so they are merged
                # into the copy already held.
                seen[key]["_props"].append(props)
                counts["duplicate occurrence"] += 1
                continue
            seen[key] = {"geom": geom, "_props": [props],
                         "file": os.path.basename(path)}

    log("{} file(s) of producer geodata, {:,} distinct feature(s)".format(
        files_read, len(seen)))
    if counts["duplicate occurrence"]:
        log("  {:,} duplicate occurrence(s) merged - a block feeding several "
            "booms is exported once per boom".format(
                counts["duplicate occurrence"]))
    if counts["longitude normalised"]:
        log("  {:,} had longitude in 0-360 and were normalised".format(
            counts["longitude normalised"]))

    kept, skipped_month = [], 0
    for rec in seen.values():
        props = rec["_props"][0]
        # Products and dates pool across every occurrence of this block.
        products, months = [], set()
        first = last = ""
        for p in rec["_props"]:
            products.extend(p.get("Products") or [])
            m, a, b = _months(p)
            months |= m
            if a and (not first or a < first):
                first = a
            if b and (not last or b > last):
                last = b

        if month and month not in months:
            skipped_month += 1
            continue

        geom, repaired = _repair(rec["geom"])
        if repaired:
            counts["geometry repaired"] += 1
        area = area_ha(geom)

        notes = []
        n = _date_note(props)
        if n:
            notes.append(n)
            counts["date irregularity"] += 1
        gtype = geom.get("type", "")
        if gtype in ("Point", "MultiPoint"):
            notes.append("a point, with no boundary - the producer gave a "
                         "location rather than an area")
            counts["point, no boundary"] += 1
        elif area < MIN_AREA_HA:
            notes.append("{:.3f} ha - too small to be a harvest area".format(
                area))
            counts["sliver"] += 1

        marks = sorted({str(p.get("Timbermark") or "").strip()
                        for p in products
                        if str(p.get("Timbermark") or "").strip()})
        name, name_source, name_note = producer_of(props, mark_lookup)
        if name_note:
            notes.append(name_note)
            counts["placeholder producer"] += 1
        volume = sum(float(p.get("NetVolume_m3") or 0) for p in products)
        species = sorted({str(p.get("CommonName") or "").strip()
                          for p in products if p.get("CommonName")})

        kept.append({"type": "Feature", "geometry": geom, "properties": {
            "ProducerName": name,
            "ProducerCountry": str(props.get("CountryOfProduction")
                                   or "").strip(),
            "harp_producer_source": name_source,
            "harp_supplier": ORIGINATOR_NAMES.get(
                str(props.get("Originator") or "").lower(),
                str(props.get("Originator") or "")),
            "harp_supplier_code": source_id_of(rec),
            "harp_geometry_kind": "producer_declared",
            "harp_method": "declared by the producer",
            "harp_source_system": str(props.get("Originator") or ""),
            "harp_key": source_id_of(rec),
            "harp_timber_mark": "; ".join(marks),
            "harp_area_ha": round(area, 4),
            "harp_tier": "P1d",
            "harp_traceability": "declared",
            "harp_is_envelope": False,
            "harp_declared_by_supplier": True,
            "harp_production_from": first,
            "harp_production_to": last,
            "harp_production_months": " ".join(sorted(months)),
            "harp_volume_m3": round(volume, 3),
            "harp_species": "; ".join(species),
            "harp_boom": str(props.get("BoomName") or ""),
            "harp_source_file": rec["file"],
            "harp_data_note": "; ".join(notes),
            "harp_note": ("a harvest area the producer declared. Taken at "
                          "their word - nothing here was checked against a "
                          "register."),
        }})

    if month:
        log("  {:,} kept for {}, {:,} outside it".format(
            len(kept), month, skipped_month))

    shippable = [f for f in kept
                 if f["properties"]["harp_area_ha"] >= MIN_AREA_HA]
    if len(shippable) != len(kept):
        log("")
        log("  {:,} feature(s) carry no usable area - {} point(s) and {} "
            "sliver(s). Kept and annotated rather than dropped.".format(
                len(kept) - len(shippable), counts["point, no boundary"],
                counts["sliver"]))
    for label in ("geometry repaired", "date irregularity",
                  "placeholder producer"):
        if counts[label]:
            log("  {:,} {}".format(counts[label], label))

    return kept, {"files": files_read, "distinct": len(seen),
                  "kept": len(kept), "counts": dict(counts),
                  "unshippable": len(kept) - len(shippable)}


def source_id_of(rec: dict) -> str:
    return str((rec["_props"][0] or {}).get("SourceID") or "").strip()
