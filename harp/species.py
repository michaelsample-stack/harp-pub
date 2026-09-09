"""What was growing on a harvest area, from national species rasters.

    from harp import species
    features, report = species.apply(features, cfg, log=log)

Two rasters, one method. Every feature is turned into a polygon, the raster is
read over it, and the pixels are counted by class - the histogram is the
composition.

    Canada   projects/sat-io/open-datasets/CA_FOREST/SPECIES-1984-2022
             annual leading species, 30 m, 1984-2022, from Landsat time series
             trained on National Forest Inventory plots. 86.1% accurate
             against independent validation.

    US       projects/gtac-data-publish/assets/TreeMap/Product_Version/2026-1
             FIA forest type, 30 m, imputed from FIA plots across the
             landscape.

THE TWO ARE NOT LIKE FOR LIKE
-----------------------------
Canada classifies the leading **species** in each pixel. TreeMap has no species
band at all - it carries the forest **type**, which is named for its leading
species but classifies the stand.

The practical effect shows in the output and is worth knowing before comparing
across the border. Two features of the same size and pixel count:

    Canada   Western hemlock 79%; Douglas-fir 21%
    US       California mixed conifer 62%; California laurel 13%; Tanoak 9%;
             Red alder 8%; California black oak 7%

Neither is wrong. Adjacent pixels within one stand get the same species in
Canada, so a small area reads uniform; neighbouring FIA plots get imputed
different forest types, so the same ground reads mixed. **The percentages do
not mean the same thing on each side and should not be averaged together.**

A few FIA types are group labels rather than species - California mixed
conifer, misc western softwoods, other hardwoods. They carry no genus, because
there honestly is not one, and their HS code follows whether the group is
coniferous.

WHY A RASTER RATHER THAN THE PROVINCIAL INVENTORY
--------------------------------------------------
The BC vegetation inventory was tried first and works, and where it has
attributes it is finer - a surveyed stand boundary with a percentage per
species beats a pixel count.

It failed on the case that matters most. Of 171 harvest polygons, 48 overlapped
inventory carrying no species at all: they were detections of recent harvest,
and the inventory had already cleared those polygons because it knew the ground
had been cut. The one thing we want to know is the thing it blanks.

An annual raster does not have that problem.

WHICH YEAR
----------
The year before the harvest, from whatever date the feature carries. A stand
cut in March was standing the previous summer, which is what that year's
classification saw. Failing that, the most recent year the raster covers.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter

try:
    import ee
except ImportError:  # pragma: no cover - the package is optional
    ee = None

CA_COLLECTION = "projects/sat-io/open-datasets/CA_FOREST/SPECIES-1984-2022"
US_IMAGE = ("projects/gtac-data-publish/assets/TreeMap/"
            "Product_Version/2026-1")
US_FALLBACKS = ["USFS/GTAC/TreeMap/v2022", "USFS/GTAC/TreeMap/v2016"]
US_BAND = "FORTYPCD"

CA_FIRST_YEAR, CA_LAST_YEAR = 1984, 2022

MIN_SHARE = 5.0
THIN_PIXELS = 10
POINT_AREA_HA = 4.0
BATCH = 200
SCALE = 30

US_JURISDICTIONS = {"WA", "WASHINGTON", "OR", "OREGON", "CA", "CALIFORNIA",
                    "AK", "ALASKA", "ID", "IDAHO", "MT", "MONTANA", "US",
                    "USA"}

# Canada's class values, from Species_Names.xlsx published with the dataset.
#
# Unlike TreeMap this asset carries no class table of its own - one band, five
# properties, nothing to check against. The spreadsheet is the only authority
# and these names cannot be verified from the data.
#
# Class 0 is Nontreed and is excluded downstream.
CA_SPECIES = {
    1: ("Amabilis fir", "Abies", "amabilis"),
    2: ("Balsam fir", "Abies", "balsamea"),
    3: ("Subalpine fir", "Abies", "lasiocarpa"),
    4: ("Bigleaf maple", "Acer", "macrophyllum"),
    5: ("Red maple", "Acer", "rubrum"),
    6: ("Sugar maple", "Acer", "saccharum"),
    7: ("Gray alder", "Alnus", "incana"),
    8: ("Red alder", "Alnus", "rubra"),
    9: ("Yellow birch", "Betula", "alleghaniensis"),
    10: ("White birch", "Betula", "papyrifera"),
    11: ("Yellow-cedar", "Chamaecyparis", "nootkatensis"),
    12: ("Black ash", "Fraxinus", "nigra"),
    13: ("Tamarack", "Larix", "laricina"),
    14: ("Western larch", "Larix", "occidentalis"),
    15: ("Norway spruce", "Picea", "abies"),
    16: ("Engelmann spruce", "Picea", "engelmannii"),
    17: ("White spruce", "Picea", "glauca"),
    18: ("Black spruce", "Picea", "mariana"),
    19: ("Red spruce", "Picea", "rubens"),
    20: ("Sitka spruce", "Picea", "sitchensis"),
    21: ("Whitebark pine", "Pinus", "albicaulis"),
    22: ("Jack pine", "Pinus", "banksiana"),
    23: ("Lodgepole pine", "Pinus", "contorta"),
    24: ("Ponderosa pine", "Pinus", "ponderosa"),
    25: ("Red pine", "Pinus", "resinosa"),
    26: ("Eastern white pine", "Pinus", "strobus"),
    27: ("Balsam poplar", "Populus", "balsamifera"),
    28: ("Largetooth aspen", "Populus", "grandidentata"),
    29: ("Trembling aspen", "Populus", "tremuloides"),
    30: ("Douglas-fir", "Pseudotsuga", "menziesii"),
    31: ("Red oak", "Quercus", "rubra"),
    32: ("Eastern white-cedar", "Thuja", "occidentalis"),
    33: ("Western redcedar", "Thuja", "plicata"),
    34: ("Eastern hemlock", "Tsuga", "canadensis"),
    35: ("Western hemlock", "Tsuga", "heterophylla"),
    36: ("Mountain hemlock", "Tsuga", "mertensiana"),
    37: ("White elm", "Ulmus", "americana"),
}

# TreeMap's FORTYPCD. The asset publishes its own names - 191 of them - so
# this table exists only to supply the genus and species the asset does not
# carry, and as a fallback if the published table cannot be read.
US_FOREST_TYPE = {
    201: ("Douglas-fir", "Pseudotsuga", "menziesii"),
    202: ("Port-Orford-cedar", "Chamaecyparis", "lawsoniana"),
    221: ("ponderosa pine", "Pinus", "ponderosa"),
    222: ("incense-cedar", "Calocedrus", "decurrens"),
    224: ("sugar pine", "Pinus", "lambertiana"),
    225: ("Jeffrey pine", "Pinus", "jeffreyi"),
    226: ("Coulter pine", "Pinus", "coulteri"),
    241: ("western white pine", "Pinus", "monticola"),
    261: ("white fir", "Abies", "concolor"),
    262: ("red fir", "Abies", "magnifica"),
    263: ("noble fir", "Abies", "procera"),
    264: ("Pacific silver fir", "Abies", "amabilis"),
    265: ("Engelmann spruce", "Picea", "engelmannii"),
    266: ("Engelmann spruce / subalpine fir", "Picea", "engelmannii"),
    267: ("grand fir", "Abies", "grandis"),
    268: ("subalpine fir", "Abies", "lasiocarpa"),
    269: ("blue spruce", "Picea", "pungens"),
    270: ("mountain hemlock", "Tsuga", "mertensiana"),
    271: ("Alaska yellow-cedar", "Chamaecyparis", "nootkatensis"),
    281: ("lodgepole pine", "Pinus", "contorta"),
    301: ("western hemlock", "Tsuga", "heterophylla"),
    304: ("western redcedar", "Thuja", "plicata"),
    305: ("Sitka spruce", "Picea", "sitchensis"),
    321: ("western larch", "Larix", "occidentalis"),
    341: ("redwood", "Sequoia", "sempervirens"),
    361: ("knobcone pine", "Pinus", "attenuata"),
    366: ("limber pine", "Pinus", "flexilis"),
    367: ("whitebark pine", "Pinus", "albicaulis"),
    369: ("western juniper", "Juniperus", "occidentalis"),
    703: ("cottonwood", "Populus", "spp."),
    704: ("willow", "Salix", "spp."),
    722: ("Oregon ash", "Fraxinus", "latifolia"),
    901: ("aspen", "Populus", "tremuloides"),
    902: ("paper birch", "Betula", "papyrifera"),
    904: ("balsam poplar", "Populus", "balsamifera"),
    911: ("red alder", "Alnus", "rubra"),
    912: ("bigleaf maple", "Acer", "macrophyllum"),
    922: ("California black oak", "Quercus", "kelloggii"),
    923: ("Oregon white oak", "Quercus", "garryana"),
    941: ("tanoak", "Notholithocarpus", "densiflorus"),
    942: ("California laurel", "Umbellularia", "californica"),
    943: ("giant chinkapin", "Chrysolepis", "chrysophylla"),
    961: ("Pacific madrone", "Arbutus", "menziesii"),
    # Group labels rather than species - see GROUP_TYPES. Named here so the
    # fallback reads sensibly when the published table cannot be reached.
    368: ("Misc western softwoods", "", ""),
    371: ("California mixed conifer", "", ""),
    962: ("Other hardwoods", "", ""),
}

# Forest types that name a group rather than a species. They carry no genus
# because there is not one, and their HS code follows whether the group is
# coniferous - otherwise 62% of a Californian plot ships as "other
# non-coniferous wood" for something entirely coniferous.
GROUP_TYPES = {
    368: "4403.24",   # misc western softwoods
    371: "4403.24",   # California mixed conifer
    962: "4403.99",   # other hardwoods
    999: "",          # nonstocked - excluded entirely
}

# Genus to HS subheading. 4403.2x coniferous, 4403.9x not.
#
# Chamaecyparis and Callitropsis are the same tree; yellow-cedar was moved
# between genera and the two published tables disagree about which to use.
HS = {
    "Pseudotsuga": "4403.24", "Tsuga": "4403.26", "Thuja": "4403.26",
    "Chamaecyparis": "4403.26", "Callitropsis": "4403.26",
    "Calocedrus": "4403.26", "Picea": "4403.24", "Abies": "4403.24",
    "Pinus": "4403.24", "Larix": "4403.24", "Taxus": "4403.24",
    "Sequoia": "4403.24", "Juniperus": "4403.24",
    "Populus": "4403.97", "Betula": "4403.95", "Quercus": "4403.91",
    "Fagus": "4403.92",
}
HS_OTHER = "4403.99"


def settings(cfg) -> dict:
    """Species options, with sensible defaults.

    On by default. A run without Earth Engine reachable says so and carries
    on; species is an addition to a month, not a precondition for one.
    """
    sp = ((getattr(cfg, "sources", None) or {}).get("species") or {})
    return {
        "enabled": sp.get("enabled", True),
        "project": sp.get("project") or "",
        "ca_collection": sp.get("ca_collection") or CA_COLLECTION,
        "us_image": sp.get("us_image") or US_IMAGE,
        "scale": int(sp.get("scale", SCALE)),
        "min_share": float(sp.get("min_share", MIN_SHARE)),
    }


# ────────────────────────────── names ──────────────────────────────────────

def species_name(code, table) -> tuple:
    """Common name, genus, species, HS code for a class value."""
    try:
        c = int(float(code))
    except (TypeError, ValueError):
        return (str(code), "", "", "")
    if c in GROUP_TYPES and c not in (999,):
        name = table.get(c, ("group type {}".format(c), "", ""))[0]
        return (name, "", "", GROUP_TYPES[c])
    if c in table:
        name, genus, sp = table[c]
        return (name, genus, sp, HS.get(genus, HS_OTHER))
    return ("class {}".format(c), "", "", "")


def _as_list(value) -> list:
    """A class table, however the publisher stored it.

    The same key holds a list of 142 values on USFS/GTAC/TreeMap/v2016 and a
    single comma-separated string on the gtac-data-publish copy of the same
    image. Taking the length of the string form gives one, which passes a
    naive length check and builds a table of one nonsense entry.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def published_classes(img, band: str) -> dict:
    """The class names an asset publishes for a band.

    Must be given a source image, not a mosaic - mosaicking produces a new
    image with no properties of its own.
    """
    try:
        props = img.getInfo().get("properties") or {}
    except Exception:
        return {}
    values = _as_list(props.get("{}_class_values".format(band)))
    names = _as_list(props.get("{}_class_names".format(band)))
    if not values or not names or len(values) != len(names):
        return {}
    out = {}
    for v, n in zip(values, names):
        try:
            out[int(float(str(v).strip()))] = str(n).strip()
        except (TypeError, ValueError):
            continue
    return out


def published_classes_in(collection, band: str, log=print) -> dict:
    """The class table from whichever image in a collection carries it.

    Not every image does. TreeMap publishes its tables on the 2016 image only;
    the later vintages carry eight properties and none, so reading from the
    newest - the one the pixels come from - finds nothing.
    """
    try:
        n = min(collection.size().getInfo(), 8)
    except Exception:
        n = 1
    try:
        ids = collection.aggregate_array("system:index").getInfo()
    except Exception:
        ids = []
    for i in range(n):
        try:
            img = ee.Image(collection.toList(n).get(i))
        except Exception:
            continue
        table = published_classes(img, band)
        if table:
            log("  {} class name(s) from {}".format(
                len(table), ids[i] if i < len(ids) else "image {}".format(i)))
            return table
    return {}


def merge_classes(published: dict, fallback: dict) -> dict:
    """Published names, with genus and species filled in from the table.

    The asset names a class; it does not give the botanical name, and the
    EUDR output wants both.
    """
    out = {}
    for code, name in published.items():
        _n, genus, sp = fallback.get(code, ("", "", ""))
        out[code] = (name, genus, sp)
    for code, triple in fallback.items():
        out.setdefault(code, triple)
    return out


# ─────────────────────────── reading geometry ──────────────────────────────

def is_us(feature: dict) -> bool:
    """Which raster to read this feature from.

    Two fields can say where a feature is and they disagree about one value.
    `harp_jurisdiction` is a state or province, so CA there means California.
    `ProducerCountry` is ISO 3166-1 alpha-2, so CA there means Canada.

    Reading them as one field sends every Canadian feature to the US raster
    wherever the jurisdiction is blank - which is the case for anything built
    before jurisdictions were set on search areas, and for any file that has
    been through the EUDR projection.
    """
    p = feature.get("properties") or {}

    j = str(p.get("harp_jurisdiction") or "").strip().upper()
    if j:
        return j in US_JURISDICTIONS

    country = str(p.get("ProducerCountry") or "").strip().upper()
    if country in ("CA", "CAN", "CANADA"):
        return False
    if country in ("US", "USA", "UNITED STATES"):
        return True
    return False


def read_geometry(feature: dict) -> tuple:
    """The geometry to read the raster with, and what it stands for.

    A polygon reads as itself. A point has no area, so it is read as a circle
    of the area it states - a point read as a point lands on one 30 m cell,
    nine hundredths of a hectare, and returns that cell's class at a hundred
    percent. The circle is never written back as the feature's geometry.
    """
    geom = feature.get("geometry") or {}
    kind = geom.get("type")
    if kind in ("Polygon", "MultiPolygon"):
        return geom, 0.0
    if kind not in ("Point", "MultiPoint"):
        return None, 0.0

    coords = geom.get("coordinates") or []
    if kind == "MultiPoint":
        if not coords:
            return None, 0.0
        lon, lat = coords[0][0], coords[0][1]
    else:
        if len(coords) < 2:
            return None, 0.0
        lon, lat = coords[0], coords[1]

    props = feature.get("properties") or {}
    ha = 0.0
    for key in ("harp_area_ha", "Area", "area_ha"):
        try:
            ha = float(props.get(key) or 0)
        except (TypeError, ValueError):
            ha = 0.0
        if ha > 0:
            break
    if ha <= 0:
        ha = POINT_AREA_HA

    radius = math.sqrt(ha * 10_000.0 / math.pi)
    dlat = radius / 111_320.0
    dlon = radius / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    ring = [[lon + dlon * math.cos(2 * math.pi * i / 32.0),
             lat + dlat * math.sin(2 * math.pi * i / 32.0)]
            for i in range(33)]
    return {"type": "Polygon", "coordinates": [ring]}, ha


def year_for(feature: dict) -> tuple:
    """Which year of the raster to read, and why."""
    p = feature.get("properties") or {}
    for key in ("HarvestStartDate", "harp_detected_first",
                "harp_declared_start", "harp_production_from"):
        v = str(p.get(key) or "").strip()[:4]
        if len(v) == 4 and v.isdigit():
            return (max(CA_FIRST_YEAR, min(int(v) - 1, CA_LAST_YEAR)),
                    "the year before the harvest date on this feature")
    return (CA_LAST_YEAR, "the most recent year the raster covers - this "
                          "feature carries no harvest date")


# ──────────────────────────────── reading ──────────────────────────────────

def histogram(features: list[dict], image, scale: int = SCALE,
              log=print) -> dict:
    """Pixel counts by class for each feature, in one call per batch.

    reduceRegions does the work server-side, so the geometry goes up and a
    handful of counts come back - not the raster.
    """
    out = {}
    for start in range(0, len(features), BATCH):
        chunk = features[start:start + BATCH]
        members = []
        for i, f in enumerate(chunk):
            geom, _ha = read_geometry(f)
            if geom is None:
                continue
            members.append(ee.Feature(ee.Geometry(geom),
                                      {"harp_idx": i + start}))
        if not members:
            continue
        try:
            reduced = image.reduceRegions(
                collection=ee.FeatureCollection(members),
                reducer=ee.Reducer.frequencyHistogram(),
                scale=scale).getInfo()
        except Exception as exc:
            log("    features {}-{} failed: {}".format(
                start, start + len(chunk), str(exc).splitlines()[0][:110]))
            continue
        for f in reduced.get("features") or []:
            p = f.get("properties") or {}
            idx, hist = p.get("harp_idx"), p.get("histogram")
            if idx is not None and hist:
                out[idx] = hist
    return out


def describe(hist: dict, table, min_share: float = MIN_SHARE) -> tuple:
    """A pixel histogram as a dominant, a readable mix, and a structured list.

    Canada's 0 is Nontreed and TreeMap's 999 is Nonstocked. Neither is a
    species, and counting them would dilute everything else.
    """
    counts = {}
    for k, v in (hist or {}).items():
        try:
            code, n = int(float(k)), float(v)
        except (TypeError, ValueError):
            continue
        if code <= 0 or code == 999 or n <= 0:
            continue
        counts[code] = counts.get(code, 0.0) + n
    if not counts:
        return "", "", [], 0

    total = sum(counts.values())
    pcts = {c: n / total * 100.0 for c, n in counts.items()}
    kept = {c: p for c, p in pcts.items() if p >= min_share}
    if not kept:
        kept = dict(sorted(pcts.items(), key=lambda kv: -kv[1])[:1])
    kept_total = sum(kept.values())
    ordered = sorted(kept.items(), key=lambda kv: -kv[1])

    parts, structured = [], []
    for code, pct in ordered:
        name, genus, sp, hs = species_name(code, table)
        share = round(pct / kept_total * 100.0, 1)
        parts.append("{} {:.0f}%".format(name, share))
        structured.append({"CommonName": name, "Genus": genus, "Species": sp,
                           "HSCode": hs, "ClassCode": code,
                           "ProportionPct": share})
    return (species_name(ordered[0][0], table)[0], "; ".join(parts),
            structured, int(total))


def us_image(asset: str, log=print) -> tuple:
    """The TreeMap image, whichever id resolves.

    Returns (mosaic, band, asset, collection). TreeMap is published as a
    collection - one image per version - and the error for loading it as an
    image reads like the asset is missing. It also holds CONUS separately
    from the islands, so the study area is filtered rather than mosaicked
    whole.
    """
    last = None
    for candidate in [asset] + [a for a in US_FALLBACKS if a != asset]:
        img, bands, coll = None, None, None
        for as_collection in (False, True):
            try:
                if as_collection:
                    coll = ee.ImageCollection(candidate)
                    filtered = coll
                    try:
                        areas = [v for v in coll.aggregate_array(
                            "study_area").getInfo() if v]
                        if "CONUS" in areas:
                            filtered = filtered.filter(
                                ee.Filter.eq("study_area", "CONUS"))
                    except Exception:
                        pass
                    try:
                        years = sorted({v for v in filtered.aggregate_array(
                            "year").getInfo() if v is not None})
                        if len(years) > 1:
                            filtered = filtered.filter(
                                ee.Filter.eq("year", years[-1]))
                    except Exception:
                        pass
                    cand = filtered.mosaic()
                else:
                    cand = ee.Image(candidate)
                bands = cand.bandNames().getInfo()
                img = cand
                break
            except Exception as exc:
                last = exc
        if img is None:
            continue
        band = US_BAND if US_BAND in bands else None
        if band is None:
            for alt in ("FORTYPCD", "FLDTYPCD", "SPCD", "SPECIES"):
                if alt in bands:
                    band = alt
                    break
        if band is None:
            log("  {} has no forest type band.".format(candidate))
            log("    bands: {}".format(", ".join(sorted(bands))))
            continue
        if candidate != asset:
            log("  using {} - {} did not resolve".format(candidate, asset))
        return img, band, candidate, coll
    raise RuntimeError(
        "no TreeMap asset could be read, as an image or a collection. "
        "Last error: {}".format(
            str(last).splitlines()[0][:160] if last else "none"))


# ──────────────────────────────── the stage ────────────────────────────────

def apply(features: list[dict], cfg, log=print) -> tuple:
    """Species composition on every feature that can carry one."""
    opts = settings(cfg)
    if not opts["enabled"]:
        log("species is switched off in config")
        return features, {"enabled": False}
    if ee is None:
        log("earthengine-api is not installed, so no species were read.")
        log("  pip install earthengine-api")
        return features, {"enabled": True, "why": "earthengine-api missing"}

    try:
        if opts["project"]:
            ee.Initialize(project=opts["project"])
        else:
            ee.Initialize()
    except Exception as exc:
        first = str(exc).splitlines()[0]
        if "not registered to use Earth Engine" in first:
            log("the project '{}' is not registered for Earth Engine. That is "
                "a one-time setup step:".format(opts["project"]))
            log("  https://console.cloud.google.com/earth-engine/"
                "configuration?project={}".format(opts["project"]))
        else:
            log("could not start Earth Engine: {}".format(first[:160]))
        log("  The month keeps its geometry and goes on without species.")
        return features, {"enabled": True, "why": first[:160]}

    ca, us, skipped = [], [], 0
    for i, f in enumerate(features):
        if (f.get("geometry") or {}).get("type") not in (
                "Polygon", "MultiPolygon", "Point", "MultiPoint"):
            f["properties"]["harp_species_basis"] = "no usable geometry"
            skipped += 1
            continue
        (us if is_us(f) else ca).append(i)
    log("{:,} in Canada, {:,} in the United States{}".format(
        len(ca), len(us),
        ", {} with no usable geometry".format(skipped) if skipped else ""))

    results = {}

    if ca:
        coll = ee.ImageCollection(opts["ca_collection"])
        by_year = {}
        for i in ca:
            y, why = year_for(features[i])
            by_year.setdefault(y, []).append(i)
            features[i]["properties"]["_why_year"] = why
        log("")
        log("Canada - {} year(s) of the raster".format(len(by_year)))
        for y in sorted(by_year):
            idxs = by_year[y]
            img = coll.filterDate("{}-01-01".format(y),
                                  "{}-12-31".format(y)).first()
            if img is None:
                log("  {} - no image".format(y))
                continue
            log("  {}  {:,} feature(s)".format(y, len(idxs)))
            sub = [features[i] for i in idxs]
            for local, hist in histogram(sub, ee.Image(img),
                                         opts["scale"], log=log).items():
                results[idxs[local]] = (hist, CA_SPECIES, y, "Canada")

    if us:
        log("")
        log("United States - {:,} feature(s)".format(len(us)))
        try:
            img, band, asset, coll = us_image(opts["us_image"], log=log)
        except RuntimeError as exc:
            log("  {}".format(exc))
            img = None
        if img is not None:
            label = "TreeMap {} forest type".format(
                asset.rstrip("/").split("/")[-1])
            # The asset's own class names where it has them, which is the
            # authority. The table in this file then supplies the genus and
            # species the asset does not carry.
            published = (published_classes_in(coll, band, log=log)
                         if coll is not None else published_classes(img, band))
            table = (merge_classes(published, US_FOREST_TYPE) if published
                     else US_FOREST_TYPE)
            if not published:
                log("  the asset publishes no class names - using the built-in "
                    "table")
            sub = [features[i] for i in us]
            for local, hist in histogram(sub, img.select(band),
                                         opts["scale"], log=log).items():
                results[us[local]] = (hist, table, "", label)

    done, empty = 0, 0
    for i, f in enumerate(features):
        p = f["properties"]
        why = p.pop("_why_year", "")
        if i not in results:
            if not p.get("harp_species_basis"):
                p["harp_species_basis"] = (
                    "the raster returned nothing here - outside its coverage, "
                    "or too small to contain a pixel")
                empty += 1
            continue
        hist, table, year, source = results[i]
        dominant, readable, structured, pixels = describe(
            hist, table, opts["min_share"])
        if not dominant:
            p["harp_species_basis"] = (
                "{} pixel(s) read but none carried a species".format(pixels))
            empty += 1
            continue
        _geom, circle_ha = read_geometry(f)
        p["harp_species_dominant"] = dominant
        p["harp_species"] = readable
        p["harp_species_json"] = json.dumps(structured)
        p["harp_species_basis"] = "{}{}, {} pixel(s) at {} m{}{}{}".format(
            source, " {}".format(year) if year else "", pixels, opts["scale"],
            ", read from a {:.1f} ha circle since this is a point with no "
            "boundary".format(circle_ha) if circle_ha else "",
            ". Too few pixels for the proportions to mean much - read the "
            "dominant species and no more" if pixels < THIN_PIXELS else "",
            " - {}".format(why) if why and source == "Canada" else "")
        done += 1

    log("")
    log("{:,} of {:,} feature(s) carry a species mix".format(done,
                                                             len(features)))
    if empty:
        log("  {:,} returned no usable pixels".format(empty))
    if skipped:
        log("  {:,} had no usable geometry".format(skipped))

    top = Counter(f["properties"].get("harp_species_dominant")
                  for f in features if f["properties"].get("harp_species"))
    for name, n in top.most_common(6):
        log("  {:>6}  {}".format(n, name))

    return features, {"enabled": True, "with_species": done, "empty": empty,
                      "canada": len(ca), "us": len(us),
                      "dominant": dict(top.most_common(12))}
