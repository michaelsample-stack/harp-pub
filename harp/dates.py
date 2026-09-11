"""Harvest dates, and where each one came from.

    from harp import dates
    features, report = dates.apply(features, start, end, cfg, log=log)

WHY THIS EXISTS
---------------
The client's downstream customers want a harvest start and end date on every
feature. Most of the month already has one: anything change detection produced
carries the day the satellite first saw the disturbance, and a producer's own
file states its own dates.

What has no date is the tier that needed no detection - a block resolved
straight from a timber mark. The forest register holds a disturbance date field
and it is barely populated: filtering 3,339 blocks to the last two years left
fifteen. So the date has to come from somewhere else, and the only somewhere
else is the same detection service.

THE SECOND CALL
---------------
Every P1a block is buffered, the buffers are dissolved into **one feature**,
and that single MultiPolygon is submitted for the month's window. One request,
one poll, one return - the parts of the multipolygon are however many disjoint
clusters the buffering produced, and the service neither knows nor cares.

The buffer exists so that a detection sitting slightly off a block boundary is
still found. It is a search margin, not a claim about the block, and nothing
buffered is ever declared.

MATCHING, AND WHY IT DIFFERS BY GEOMETRY
----------------------------------------
Most of what comes back is points - 644 of 1,123 in one month - because a
disturbance under four hectares has no boundary in the return.

    a polygon detection   intersects at least 30% of the block's area
    a point detection     falls inside the block

Thirty percent of nothing is nothing, so a proportional test cannot be applied
to a point. Being inside is the whole test, with a metre or two of tolerance
for coordinate rounding.

**A polygon is the better observation.** It says a third of this block was cut;
a point says something small changed somewhere in it. Where both hit the same
block, the polygon's date is used, and the basis field records which answered.

**Where several detections hit one block, the earliest wins.** A block cut over
three weeks shows several; the first is when cutting started, which is what a
start date should mean.

THE BRACKET
-----------
A detection date is the day a satellite observed a change. It is not the day
cutting started and it is not the day it finished - cloud can delay an
observation by weeks, and the operation may have run for a month before
anything was visible.

The client's convention is one month either side. That is what these fields
carry, and `harp_harvest_basis` says so on every feature, because
`HarvestStartDate` reads as a fact and this one is derived.

The raw observation is kept on `harp_detected_first` beside it. Anybody who
wants to know what the bracket was built from can see it.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timedelta

# One month either side of the observation. The client's convention.
BRACKET_DAYS = 30

# How much of a block a polygon detection must cover before it dates it.
# Below this the detection is more plausibly in the block than of it.
MIN_OVERLAP = 0.30

# Coordinate rounding, not a search margin. A point is either in the block or
# it is not.
POINT_TOLERANCE_M = 2.0

# How far around a block to look. Wide enough that a detection sitting just
# off the boundary is still found, narrow enough that the dissolved shape does
# not become one blob covering the province.
BUFFER_M = 250.0


def bracket(observed) -> tuple:
    """A start and end date around an observation.

    Returns ISO strings. The dates are derived and the basis field says so;
    the field names are what the client asked for.
    """
    if isinstance(observed, str):
        try:
            observed = date.fromisoformat(observed[:10])
        except ValueError:
            return "", ""
    if not isinstance(observed, date):
        return "", ""
    return ((observed - timedelta(days=BRACKET_DAYS)).isoformat(),
            (observed + timedelta(days=BRACKET_DAYS)).isoformat())


def _basis(kind: str) -> str:
    return {
        "polygon": "detection bracketed {} days either side (polygon "
                   "overlap)".format(BRACKET_DAYS),
        "point": "detection bracketed {} days either side (point within the "
                 "block)".format(BRACKET_DAYS),
        "detection": "detection bracketed {} days either side".format(
            BRACKET_DAYS),
        "declared": "stated by the producer",
        "none": "no observation places this harvest in the window",
    }.get(kind, kind)


# ─────────────────────────── the straightforward ones ──────────────────────

def from_detection(features: list[dict], log=print) -> int:
    """Bracket whatever already carries a detection date.

    P1c, P2b and P3b arrive with `harp_detected_first` from the join back.
    """
    n = 0
    for f in features:
        p = f["properties"]
        if p.get("HarvestStartDate"):
            continue
        seen = p.get("harp_detected_first")
        if not seen:
            continue
        start, end = bracket(seen)
        if start:
            p["HarvestStartDate"], p["HarvestEndDate"] = start, end
            p["harp_harvest_basis"] = _basis("detection")
            n += 1
    if n:
        log("  {:,} dated from their own detection".format(n))
    return n


def from_declaration(features: list[dict], log=print) -> int:
    """A producer's own dates, used as given.

    They said when they cut it. Nothing here improves on that.
    """
    n, bad = 0, [0]
    for f in features:
        p = f["properties"]
        if p.get("HarvestStartDate"):
            continue
        a = str(p.get("harp_declared_start") or "").strip()[:10]
        b = str(p.get("harp_declared_end") or "").strip()[:10]
        if not a and not b:
            continue
        if a and b and b < a:
            # The producer's own dates run backwards. Seen on 29 features in
            # one batch, traced to a handful of source records copied across
            # many files.
            #
            # Not corrected here. Swapping them would assert an order the
            # producer did not state, and shipping them as given would put an
            # end date before a start date in front of a regulator. Neither is
            # defensible, so the pair is refused and the next source is tried.
            p["harp_harvest_basis"] = (
                "the producer stated {} to {}, which runs backwards".format(
                    a, b))
            bad[0] += 1
            continue
        if b == "2001-12-31" and not a:
            # A null-date stand-in, not a harvest.
            p["harp_harvest_basis"] = "the producer's end date is a placeholder"
            bad[0] += 1
            continue
        # A file stating only one end is still better than nothing.
        p["HarvestStartDate"] = a or b
        p["HarvestEndDate"] = b or a
        p["harp_harvest_basis"] = _basis("declared")
        n += 1
    if n:
        log("  {:,} dated by the producer".format(n))
    if bad[0]:
        log("  {:,} had producer dates that could not be used - they will be "
            "dated from detection instead if anything is found".format(bad[0]))
    return n


# ────────────────────────── the second detection call ──────────────────────

def _to_metres(geom):
    """Project to something where a buffer in metres means metres."""
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform
    s = shape(geom)
    lon, lat = s.centroid.x, s.centroid.y
    crs = pyproj.CRS.from_proj4(
        "+proj=aeqd +lat_0={} +lon_0={} +datum=WGS84 +units=m +no_defs".format(
            lat, lon))
    fwd = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    back = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    return transform(fwd, s), back


def buffered_union(features: list[dict], metres: float = BUFFER_M,
                   log=print) -> dict:
    """One feature covering every block, with room around each.

    Deliberately a single feature. Its geometry is a MultiPolygon of however
    many disjoint clusters the buffering left - blocks near each other merge,
    a block on its own stays its own part. The service takes a collection but
    there is no reason to send more than one thing, and the return is
    anonymous either way.
    """
    import pyproj
    from shapely.geometry import mapping, shape
    from shapely.ops import transform, unary_union

    shapes = []
    for f in features:
        try:
            s = shape(f["geometry"])
            if not s.is_valid:
                s = s.buffer(0)
        except Exception:
            continue
        if not s.is_empty:
            shapes.append(s)
    if not shapes:
        raise RuntimeError("nothing to buffer")

    # One projection for the whole set, centred on it. Good enough for a
    # search margin over a few hundred kilometres, and it keeps this to a
    # single transform rather than one per block.
    merged = unary_union(shapes)
    lon, lat = merged.centroid.x, merged.centroid.y
    crs = pyproj.CRS.from_proj4(
        "+proj=aeqd +lat_0={} +lon_0={} +datum=WGS84 +units=m +no_defs".format(
            lat, lon))
    fwd = pyproj.Transformer.from_crs("EPSG:4326", crs,
                                      always_xy=True).transform
    back = pyproj.Transformer.from_crs(crs, "EPSG:4326",
                                       always_xy=True).transform

    grown = unary_union([transform(fwd, s).buffer(metres) for s in shapes])
    geom = mapping(transform(back, grown))
    parts = len(geom.get("coordinates") or []) if geom["type"] == "MultiPolygon" else 1
    log("  {:,} block(s) buffered by {:.0f} m and dissolved into {} part(s)"
        .format(len(shapes), metres, parts))

    return {"type": "Feature", "geometry": geom, "properties": {
        "harp_purpose": "dating blocks that resolved without detection",
        "harp_blocks": len(shapes),
        "harp_buffer_m": metres,
        "harp_note": ("a search margin around blocks already resolved from a "
                      "timber mark. Submitted to find out when they were cut, "
                      "never declared - the blocks themselves are the "
                      "geometry."),
    }}


def match(blocks: list[dict], detections: list[dict], log=print) -> dict:
    """Which block each detection dates, and how.

    Returns {index in blocks: (iso date, kind)}. A polygon beats a point for
    the same block; the earliest date wins within a kind.
    """
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    shapes, keep = [], []
    for i, f in enumerate(blocks):
        try:
            s = shape(f["geometry"])
            if not s.is_valid:
                s = s.buffer(0)
        except Exception:
            continue
        if not s.is_empty:
            shapes.append(s)
            keep.append(i)
    if not shapes:
        return {}
    tree = STRtree(shapes)

    # A degree of latitude is about 111 km, so this is the tolerance in
    # degrees. Crude, and it only has to absorb coordinate rounding.
    tol = POINT_TOLERANCE_M / 111_000.0

    best = {}
    counts = Counter()
    for d in detections:
        geom = d.get("geometry") or {}
        try:
            ds = shape(geom)
        except Exception:
            continue
        if ds.is_empty:
            continue
        when = d.get("date")
        if isinstance(when, str):
            when = when[:10]
        elif isinstance(when, (date, datetime)):
            when = when.isoformat()[:10]
        if not when:
            continue

        is_point = ds.geom_type in ("Point", "MultiPoint")
        probe = ds.buffer(tol) if is_point else ds
        for j in tree.query(probe):
            block = shapes[j]
            if is_point:
                # No area, so no proportional test. Inside is the whole test.
                if not block.intersects(probe):
                    continue
                kind = "point"
            else:
                inter = block.intersection(ds)
                if inter.is_empty or not block.area:
                    continue
                if inter.area / block.area < MIN_OVERLAP:
                    continue
                kind = "polygon"

            idx = keep[j]
            prev = best.get(idx)
            if prev is None:
                best[idx] = (when, kind)
                counts[kind] += 1
            else:
                pw, pk = prev
                # A polygon is the better observation and takes precedence
                # whatever the dates say. Within a kind, the earliest wins:
                # a block cut over three weeks shows several detections and
                # the first is when cutting started.
                if pk == "point" and kind == "polygon":
                    best[idx] = (when, kind)
                    counts["polygon"] += 1
                    counts["point"] -= 1
                elif pk == kind and when < pw:
                    best[idx] = (when, kind)

    if counts:
        log("  {} block(s) dated: {}".format(
            len(best), ", ".join("{} by {}".format(n, k)
                                 for k, n in counts.most_common() if n > 0)))
    return best


def date_undated(features: list[dict], start: str, end: str, cfg,
                 out_dir: str = "", api_base: str = "", log=print) -> dict:
    """The second detection call, for whatever still has no dates.

    Everything here already has geometry from a register. This asks one
    question about it: was it cut in this window, and when.
    """
    from . import detection_api, io

    undated = [f for f in features
               if not f["properties"].get("HarvestStartDate")
               and (f.get("geometry") or {}).get("type") in
               ("Polygon", "MultiPolygon")]
    if not undated:
        log("  nothing left without dates")
        return {"asked": 0, "dated": 0}

    log("{:,} feature(s) still have no dates".format(len(undated)))
    try:
        probe = buffered_union(undated, log=log)
    except RuntimeError as exc:
        log("  {}".format(exc))
        return {"asked": len(undated), "dated": 0, "why": str(exc)}

    out_dir = out_dir or cfg.paths.outbox
    os.makedirs(out_dir, exist_ok=True)
    # Named like the union for the same reason: these arrive at the far end
    # in a shared bucket, and a file called "dating-probe" says nothing about
    # whose it is or what month it belongs to.
    probe_name = "DIST_DATES_{}_{}.geojson".format(
        str(getattr(cfg, "client", "") or "harp").upper(),
        str(start).replace("-", "")[:6])
    probe_path = io.write_json(
        os.path.join(out_dir, probe_name),
        {"type": "FeatureCollection", "name": "harp_dating_probe",
         "features": [probe]})
    log("  {}".format(probe_path))

    try:
        feats, raw, summary = detection_api.run(
            probe_path, start, end, out_dir,
            base=api_base or detection_api.DEFAULT_BASE, log=log)
    except detection_api.DetectionError as exc:
        # Not a failure of the run. These features keep their geometry and
        # lose only their dates, which they did not have to begin with.
        log("")
        log("  {}".format(str(exc).splitlines()[0]))
        log("  Those blocks keep their geometry and stay undated.")
        return {"asked": len(undated), "dated": 0, "why": str(exc)}

    from . import detect as detect_stage
    dets = detect_stage.read_detections(
        io.write_json(os.path.join(out_dir, "dating-detections.geojson"),
                      {"type": "FeatureCollection", "features": feats}),
        log=log)

    hits = match(undated, dets, log=log)
    for idx, (when, kind) in hits.items():
        p = undated[idx]["properties"]
        a, b = bracket(when)
        p["HarvestStartDate"], p["HarvestEndDate"] = a, b
        p["harp_detected_first"] = when
        p["harp_harvest_basis"] = _basis(kind)

    missed = len(undated) - len(hits)
    if missed:
        log("  {:,} found no detection in this window and stay undated"
            .format(missed))
        log("  A block cut in an earlier month will not appear in this one. "
            "That is the honest answer, not a failure.")
    return {"asked": len(undated), "dated": len(hits),
            "job": summary.get("job", "")}


# ──────────────────────────────── the pass ─────────────────────────────────

def apply(features: list[dict], start: str, end: str, cfg,
          out_dir: str = "", api_base: str = "", second_call: bool = True,
          log=print) -> tuple:
    """Dates on everything that can carry one, best source first."""
    log("")
    from_declaration(features, log=log)
    from_detection(features, log=log)

    report = {"asked": 0, "dated": 0}
    if second_call:
        report = date_undated(features, start, end, cfg, out_dir, api_base,
                              log=log)

    # Everything left says plainly that it has no date rather than carrying a
    # blank field that reads as an oversight.
    for f in features:
        p = f["properties"]
        if not p.get("HarvestStartDate"):
            # setdefault is not enough - the shared schema puts an empty
            # string on every field, and an empty basis says nothing where a
            # sentence would.
            if not str(p.get("harp_harvest_basis") or "").strip():
                p["harp_harvest_basis"] = _basis("none")

    # Last line of defence. Whatever produced them, a pair that runs
    # backwards is not shippable.
    reversed_pairs = 0
    for f in features:
        p = f["properties"]
        a, b = p.get("HarvestStartDate"), p.get("HarvestEndDate")
        if a and b and b < a:
            p["HarvestStartDate"] = p["HarvestEndDate"] = ""
            p["harp_harvest_basis"] = (
                "dates were derived but ran backwards, so none are given")
            reversed_pairs += 1
    if reversed_pairs:
        log("  {:,} pair(s) ran backwards and were withheld".format(
            reversed_pairs))

    bases = Counter(f["properties"].get("harp_harvest_basis", "?")
                    for f in features)
    dated = sum(1 for f in features if f["properties"].get("HarvestStartDate"))
    log("")
    log("{:,} of {:,} feature(s) carry harvest dates".format(dated,
                                                            len(features)))
    for b, n in bases.most_common():
        log("  {:>7,}  {}".format(n, b))
    report["dated_total"] = dated
    report["bases"] = dict(bases)
    return features, report
