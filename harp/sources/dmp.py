"""Digital Material Passports — the client's own filed declaration.

OFF BY DEFAULT
--------------
This module only runs when a passport folder is passed explicitly.

The passports are the output of an earlier effort by a third party, and nothing
in the files records who produced the geometry or from what. Comparing it
against what we resolve ourselves gave 26% overlap on mark-matched cut blocks
and effectively none on private parcels - which is consistent with a Crown-only
dataset, and equally consistent with a period mismatch or a different supply
stream. We cannot tell.

Polygons of unestablished provenance should not go into a declaration beside
geometry we can trace to a register. So the default is to leave them out. The
package sorter still recognises a passport and reports it, so it is visible
rather than ignored - it is simply not consumed.

Kept because the question of where those polygons came from is worth putting to
the client, and because if that source is ever identified this becomes useful
immediately.

WHAT IT IS
----------
Harmac Pacific filed two passports in December 2025, one for British Columbia
and one for Washington, covering chip intake for that January to July. Each
carries a HarvestUnitsDownloadURL pointing at the geometry behind it: three
declared units for BC, thirteen counties for WA, and roughly twelve and a half
thousand polygons underneath.

WHY IT IS NOT A RUNG
--------------------
The ladder is per source: hand it an identifier, get geometry back. A passport
has no identifier to key on. No timber mark, no source id, no supplier - the
declared unit is a region or a county, and the polygons inside carry nothing
that ties them to a supply source.

So this runs after the per-source loop and before assembly. What comes out is
geometry with no attribution, and it says so: every feature carries
`harp_provenance: client_declaration` and no `harp_source_id`.

TWO KINDS COME OUT
------------------
The download mixes individual cutblocks with much larger regional polygons.
They need different treatment, so they are separated on area:

    cutblock    already a harvest area          -> the master collection, P3
    catchment   a search area, not a harvest    -> the detection pool, P4

Neither is a plot claim. A cutblock here is real geometry that we cannot tie to
a source; a catchment is a region that has to go through change detection
before it means anything.

DEDUPLICATION IS GEOMETRIC
--------------------------
A DMP polygon has no registry identity, so `assemble` cannot key on
CUT_BLOCK_SKEY the way it does for everything else. A DMP cutblock that
overlaps something we already resolved is dropped: ours carries a timber mark
and a tenure holder, theirs carries neither, so keeping both would add a
weaker copy of a block we can already attribute.

CACHING
-------
Downloads happen once. S1Seven retains DMP data until delivery plus roughly a
week, so a link that works today may not next month - a file already fetched is
never re-fetched and never discarded.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# A polygon larger than this is a regional area rather than a cutblock. A BC
# coastal cutblock runs from under a hectare to a couple of hundred; the
# largest in Harmac's resolved set is 142 ha. Anything in the thousands is not
# a cutblock. Configurable because it is a judgement about this data.
CUTBLOCK_MAX_HA = 1000.0

# How much of a DMP cutblock must fall inside one of ours before it counts as
# the same block. Below this it is treated as new geometry.
DUPLICATE_OVERLAP = 0.50


@dataclass
class Passport:
    """One filed declaration, and where its geometry lives."""

    path: str
    id: str
    country: str = ""
    state: str = ""
    units: list[str] = field(default_factory=list)
    url: str = ""
    downloaded: str = ""

    @property
    def fetchable(self) -> bool:
        return bool(self.url)


def read_passport(path: str) -> Passport | None:
    """A passport, or None if this file is not one."""
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception:
        return None
    if not isinstance(blob, dict) or "DigitalMaterialPassport" not in blob:
        return None
    d = blob["DigitalMaterialPassport"]
    g = d.get("GeneralInformation") or {}
    return Passport(
        path=path,
        id=g.get("UserDefinedId") or os.path.basename(path),
        country=g.get("Country", ""),
        state=g.get("State", ""),
        units=[r.get("UserDefinedId") for r in (d.get("DMPReferences") or [])],
        url=d.get("HarvestUnitsDownloadURL") or "",
    )


def find(target: str) -> list[Passport]:
    """Every passport at or under a path. A single file works too."""
    if os.path.isfile(target):
        p = read_passport(target)
        return [p] if p else []
    if not os.path.isdir(target):
        return []
    out = []
    for name in sorted(os.listdir(target)):
        if name.lower().endswith(".json"):
            p = read_passport(os.path.join(target, name))
            if p:
                out.append(p)
    return out


# ────────────────────────────── geometry ───────────────────────────────────

def explode(geom: dict):
    """Every single-part geometry inside, however deeply nested.

    Collections are opened and multiparts split, so what comes out is always
    one Polygon. A GeometryCollection is not valid under EUDR at all, and a
    MultiPolygon standing for several separate cutblocks asserts they are one
    plot - neither can be left as it arrived.
    """
    if not geom:
        return
    t = geom.get("type")
    if t == "GeometryCollection":
        for g in geom.get("geometries", []):
            yield from explode(g)
    elif t == "MultiPolygon":
        for coords in geom.get("coordinates", []):
            yield {"type": "Polygon", "coordinates": coords}
    elif t == "Polygon":
        yield geom


def area_ha(geom: dict) -> float:
    """Geodesic area in hectares. Zero if shapely and pyproj are absent."""
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        s = shape(geom)
        a, _ = Geod(ellps="WGS84").geometry_area_perimeter(s)
        return abs(a) / 10000.0
    except Exception:
        return 0.0


# ────────────────────────────── downloading ────────────────────────────────

def fetch(passport: Passport, cache_dir: str, log=None) -> str | None:
    """The harvest units for one passport. Downloaded once, then kept."""
    log = log or (lambda *_: None)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", passport.id)[:80]
    dest = os.path.join(cache_dir, safe + ".geojson")

    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log("  {}: already downloaded ({:.1f} MB)".format(
            passport.id, os.path.getsize(dest) / 1024 / 1024))
        passport.downloaded = dest
        return dest
    if not passport.fetchable:
        log("  {}: no download link".format(passport.id))
        return None

    try:
        import requests
    except ImportError:
        log("  requests is not installed - cannot download")
        return None
    os.makedirs(cache_dir, exist_ok=True)
    try:
        log("  {}: downloading…".format(passport.id))
        r = requests.get(passport.url, timeout=900, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    except Exception as exc:
        log("  {}: download failed - {}".format(passport.id, str(exc)[:100]))
        if os.path.exists(dest):
            os.remove(dest)
        return None
    log("  {}: {:.1f} MB".format(passport.id,
                                 os.path.getsize(dest) / 1024 / 1024))
    passport.downloaded = dest
    return dest


# ──────────────────────────────── ingest ───────────────────────────────────

def _feature(geom: dict, props: dict, passport: Passport, unit: str,
             kind: str, area: float, threshold: float) -> dict:
    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {
            **{k: v for k, v in (props or {}).items() if k != "geometry"},
            "harp_provenance": "client_declaration",
            "harp_declared_by": passport.id,
            "harp_declared_unit": unit,
            "harp_jurisdiction": passport.state or passport.country,
            "harp_tier": "P3" if kind == "cutblock" else "P4",
            "harp_traceability": "inferred",
            "harp_area_ha": round(area, 2),
            "harp_kind": kind,
            "harp_kind_basis": "area {:,.1f} ha against a {:,.0f} ha "
                               "threshold".format(area, threshold),
            "harp_note": ("declared by the client, with no identifier tying it "
                          "to a supply source"),
        },
    }


def ingest(passports: list[Passport], cache_dir: str,
           threshold: float = CUTBLOCK_MAX_HA, log=None) -> dict[str, list]:
    """Download, explode and sort. Returns {'cutblock': [...], 'catchment': [...]}.

    Everything from every passport merges into the same two collections; the
    passport and declared unit ride along as properties, so nothing is lost.
    """
    log = log or (lambda *_: None)
    out: dict[str, list] = {"cutblock": [], "catchment": []}
    if not passports:
        return out

    log("{} passport(s) to ingest".format(len(passports)))
    for p in passports:
        path = fetch(p, cache_dir, log=log)
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                gj = json.load(fh)
        except Exception as exc:
            log("  {}: could not read the download - {}".format(p.id, exc))
            continue

        for i, f in enumerate(gj.get("features") or []):
            props = f.get("properties") or {}
            unit = (props.get("UserDefinedId") or props.get("name")
                    or "unit {}".format(i))
            n_cut = n_cat = 0
            for geom in explode(f.get("geometry") or {}):
                a = area_ha(geom)
                kind = "cutblock" if 0 < a <= threshold else "catchment"
                if a <= 0:
                    # Without shapely nothing can be sized, so nothing can be
                    # sorted. Treat it as a search area rather than assert it
                    # is a harvest.
                    kind = "catchment"
                out[kind].append(_feature(geom, props, p, unit, kind, a,
                                          threshold))
                if kind == "cutblock":
                    n_cut += 1
                else:
                    n_cat += 1
            log("    {:<44} {:>5} cutblock  {:>4} regional".format(
                unit[:44], n_cut, n_cat))

    log("  {} cutblock(s), {} regional area(s) from the declaration".format(
        len(out["cutblock"]), len(out["catchment"])))
    return out


# ──────────────────────────── deduplication ────────────────────────────────

def drop_duplicates(declared: list[dict], resolved: list[dict],
                    overlap: float = DUPLICATE_OVERLAP,
                    log=None) -> tuple[list[dict], int]:
    """Remove declared cutblocks that we have already resolved ourselves.

    Geometric, because a declared polygon carries no registry identity. A
    declared block with at least `overlap` of its area inside one of ours is
    the same block described twice - and ours carries a timber mark and a
    tenure holder where theirs carries neither, so theirs is the copy to drop.

    Returns (kept, dropped_count). Without shapely nothing is dropped, and the
    caller is told, because silently keeping duplicates would double-count
    area.
    """
    log = log or (lambda *_: None)
    if not declared or not resolved:
        return declared, 0
    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree
    except ImportError:
        log("  shapely is not installed - declared polygons cannot be checked "
            "against ours, so none are dropped and area may be double counted")
        return declared, 0

    ours = []
    for f in resolved:
        g = f.get("geometry")
        if not g:
            continue
        try:
            ours.append(shape(g))
        except Exception:
            pass
    if not ours:
        return declared, 0
    tree = STRtree(ours)

    kept, dropped = [], 0
    for f in declared:
        try:
            g = shape(f["geometry"])
        except Exception:
            kept.append(f)
            continue
        if g.is_empty or g.area <= 0:
            kept.append(f)
            continue
        best = 0.0
        for idx in tree.query(g):
            # shapely 1.x yields geometries, 2.x yields indices - and the 2.x
            # indices are numpy integers, which are not `int`. Test for the
            # thing we need rather than for a type.
            other = idx if hasattr(idx, "intersection") else ours[int(idx)]
            try:
                inter = g.intersection(other).area
            except Exception:
                continue
            best = max(best, inter / g.area)
            if best >= overlap:
                break
        if best >= overlap:
            dropped += 1
        else:
            kept.append(f)

    if dropped:
        log("  {} declared cutblock(s) dropped as already resolved from the "
            "supply record at {:.0f}% overlap".format(dropped, overlap * 100))
    return kept, dropped
