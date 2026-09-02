"""One command, one folder in, everything out.

    harp run ./data/inbox/2026-08

The stages already exist and have been driven separately by hand: sort the
drop, resolve every identifier, build a search area for whatever did not
resolve, split the result into discrete blocks and broad areas. Driving them
by hand means remembering four commands in the right order with the right
paths, and the tools quietly disagreeing about where things live.

This is the whole thing as one step. It calls the same code the separate
tools call - nothing is reimplemented, so a fix in one place is a fix
everywhere.

WHAT COMES OUT
--------------
    harvest-areas-*.geojson   resolved from an identifier - a timber mark to
                              a cut block, a private mark to its parcel. These
                              are the answer and need nothing further.
    tenure-blocks-*.geojson   real cut blocks, but from querying a company
                              rather than a purchase. Everything that company
                              cut, so far more than this client bought. Held
                              for confirmation by detection.
    search-areas-*.geojson    administrative areas. Nothing says a harvest
                              happened anywhere in particular; detection has
                              to find one.
    resolution-*.csv          one row per source, and how it resolved
    run-*.txt                 what happened, in order

Three files because the three need different handling downstream, not because
three stages produced them. The first is finished. The second gets confirmed
and dated. The third gets searched.

Both carry an identical schema, so either can be read without knowing which.
Both attribute every polygon to a supplier, including where two suppliers
share a boundary - the geometry repeats and the attribution does not, because
a detection has to be traceable to whoever supplied the fibre.

WHAT IT DOES NOT DO
-------------------
Change detection. That runs elsewhere, against a maintained table of harvest
polygons, and what comes back is folded in afterwards. Everything here is
either a harvest area or a place to look for one.
"""

from __future__ import annotations

import glob
import os
from collections import Counter
from datetime import datetime

from . import (assemble, catchments, detect as detect_stage, detection_api,
               eudr_schema, identify, io, library as library_stage, manifest,
               mills, package, router)
from .sources import private_marks, producer_geodata


# A polygon larger than this is not behaving like a cut block. BC coastal
# blocks run from under a hectare to a couple of hundred; the largest in
# Harmac's resolved set is 142 ha. Generous on purpose.
MAX_BLOCK_HA = 2000.0

# Every field both output files carry. Absent values are written empty rather
# than omitted, so the two files have the same shape and a consumer never has
# to check whether a key exists.
SCHEMA = [
    # The EUDR-cased fields, carried from resolution rather than mapped at
    # the end, so they are filled where something knew and visibly empty where
    # nothing did. ProducerCountry is here because a producer's own file
    # states it and there is no reason to rediscover it from the jurisdiction.
    "ProducerName", "ProducerCountry",
    "harp_producer_number", "harp_producer_source",
    "harp_supplier", "harp_supplier_code", "harp_jurisdiction",
    "harp_geometry_kind", "harp_method", "harp_source_system",
    "harp_key", "harp_key_name",
    "harp_timber_mark", "harp_district",
    "harp_area_ha",
    "harp_tier", "harp_is_envelope", "harp_traceability",
    "harp_declared_by_supplier", "harp_basis", "harp_note",
    # Only a producer's own file carries these. Empty elsewhere, and that is
    # the point - the shared schema means a consumer never has to ask which
    # route produced a feature.
    "harp_production_from", "harp_production_to", "harp_production_months",
    "harp_volume_m3", "harp_species", "harp_boom", "harp_source_file",
    "harp_data_note",
]

# Discrete, resolved from an identifier, and the harvest itself. Finished.
HARVEST_KINDS = {"cut_block", "producer_declared"}
# A titled parcel is the land a mark was scaled from, not the cut. A 41 ha
# median with a tail to 1,900 means declaring the parcel over-declares by a
# long way - across one month, 303,000 ha of parcel against 71,000 ha of
# detected harvest. So a parcel is a place to look, and what gets declared is
# what detection finds inside it, carrying the parcel's mark.
PARCEL_KINDS = {"parcel"}
# Discrete, but from querying a company. Real blocks, wrong scope.
TENURE_KINDS = {"tenure_block"}
# Not a harvest area at all.
SEARCH_KINDS = {"district", "county", "national_forest", "mill_buffer",
                "large_parcel", "parcel"}

KIND_FROM_METHOD = {
    "operator tenure": "tenure_block",
    "named district": "district",
    "named county": "county",
    "national forest": "national_forest",
    "mill buffer": "mill_buffer",
}


def _area_ha(geom) -> float:
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        g = Geod(ellps="WGS84")
        s = shape(geom)
        polys = [s] if s.geom_type == "Polygon" else (
            list(s.geoms) if s.geom_type == "MultiPolygon" else [])
        return sum(abs(g.geometry_area_perimeter(p)[0]) for p in polys) / 10000.0
    except Exception:
        return 0.0


def _normalise(props: dict, defaults: dict) -> dict:
    out = {}
    for k in SCHEMA:
        v = props.get(k)
        if v in (None, ""):
            v = defaults.get(k, "")
        out[k] = v
    return out


def _kind_of(props: dict) -> str:
    """What sort of geometry is this, in one word.

    THE TIER DECIDES, not the flag. An earlier version classified a
    tenure-derived block by `harp_is_envelope`, which is set only for R7.
    R6 also resolves by client number and also produces P2a, but leaves the
    flag unset - so 2,694 of its blocks were classified `cut_block`, landed
    in the harvest file, and would have been declared directly instead of
    going to detection.

    The tier is the thing that says what a piece of geometry means. Two rungs
    reaching the same tier by different routes should not sort differently,
    and any future rung reaching P2a is handled without another edit.
    """
    method = props.get("harp_method", "")
    if method in KIND_FROM_METHOD:
        return KIND_FROM_METHOD[method]

    tier = str(props.get("harp_tier", "")).strip()
    if tier == "P1d":
        return "producer_declared"
    if tier.startswith("P2"):
        # Attributable to a supplier, not to a purchase. A real register
        # block, but everything that company cut.
        return "tenure_block"
    if tier == "P1b":
        return "parcel"
    if tier.startswith("P3"):
        return "district"

    registry = str(props.get("harp_registry", "")
                   or props.get("harp_source_system", "")).lower()
    if "parcel" in registry:
        return "parcel"
    if "district" in registry and "cutblock" not in registry:
        return "district"
    # Nothing said otherwise, and the flag is the last resort rather than the
    # first test.
    if props.get("harp_is_envelope"):
        return "tenure_block"
    return "cut_block"


def _split(features: list[dict], max_block_ha: float,
           log=None) -> tuple[list, list, list, list]:
    """Harvest areas, tenure blocks, search areas, and the sizes seen.

    Size is measured rather than trusted. A parcel above the threshold is not
    a harvest area - Mosaic hold titled land running to fifteen thousand
    hectares, and declaring one as a plot would over-declare by two orders of
    magnitude. Those become search areas, but keep their timber mark, so a
    detection inside one inherits a mark and an owner.
    """
    log = log or (lambda *_: None)
    harvest, tenure, search, sizes, moved = [], [], [], [], 0

    for f in features:
        p = dict(f.get("properties") or {})
        kind = _kind_of(p)

        area = 0.0
        for key in ("harp_area_ha", "area_ha", "part_area_ha"):
            try:
                area = float(p.get(key) or 0)
            except (TypeError, ValueError):
                area = 0.0
            if area:
                break
        if not area:
            area = _area_ha(f.get("geometry"))

        props = _normalise(p, {
            "harp_geometry_kind": kind,
            # A search area has no producer of its own. Where the client's
            # name for the supplier is a real name it stands in; a bare code
            # does not, because a code can cover several companies.
            "ProducerName": (p.get("ProducerName")
                             or p.get("harp_tenure_holder") or ""),
            "harp_producer_source": (p.get("harp_producer_source")
                                     or ("forest register"
                                         if p.get("harp_tenure_holder")
                                         else "")),
            "harp_area_ha": round(area, 2),
            "harp_timber_mark": p.get("timber_mark") or p.get("TIMBER_MARK", ""),
            "harp_district": p.get("district")
                             or p.get("GEOGRAPHIC_DISTRICT_CODE", ""),
            "harp_key": p.get("harp_identifier", ""),
            "harp_source_system": p.get("harp_registry", ""),
            "harp_supplier": p.get("harp_supplier_name", ""),
            "harp_supplier_code": p.get("harp_source_id", ""),
            "harp_is_envelope": bool(p.get("harp_is_envelope")),
            "harp_traceability": p.get("harp_traceability") or "inferred",
        })
        out = {"type": "Feature", "geometry": f.get("geometry"),
               "properties": props}

        if kind in PARCEL_KINDS:
            # Every parcel, not only the oversized ones. The mark is kept, so
            # a detection inside inherits it and stays directly traceable.
            props["harp_tier"] = "P1b"
            props["harp_traceability"] = "direct"
            props["harp_note"] = (
                "the titled parcel this mark was scaled from - a place to "
                "look, not the harvest. What is declared is what detection "
                "finds inside it.")
            sizes.append(area)
            search.append(out)
        elif kind in HARVEST_KINDS or kind in TENURE_KINDS:
            sizes.append(area)
            if area > max_block_ha:
                props["harp_geometry_kind"] = "large_parcel"
                props["harp_note"] = (
                    "{:,.0f} ha - too large to be a harvest area, so it is a "
                    "place to search. The timber mark is kept, so anything "
                    "detected inside inherits it.".format(area)).strip()
                props["harp_traceability"] = "inferred"
                search.append(out)
                moved += 1
            elif kind in TENURE_KINDS:
                # A registered harvest area attributable to a supplier. Not
                # yet placed inside a delivery window - detection does that,
                # and a confirmed one becomes P2b.
                # A registered harvest area attributable to a supplier, not
                # yet placed inside a delivery window. Detection does that,
                # and a confirmed one becomes P2b.
                if props.get("harp_tier") in ("P2", "P2a", "P2b", ""):
                    props["harp_tier"] = "P2a"
                tenure.append(out)
            else:
                harvest.append(out)
        else:
            search.append(out)

    if moved:
        log("  {} polygon(s) exceeded {:,.0f} ha and became search areas, "
            "keeping their mark".format(moved, max_block_ha))

    # The harvest file is the one thing that reaches a declaration without
    # detection, so nothing indirect belongs in it. A mismatch here means the
    # kind and the tier have drifted apart again, which is exactly how 2,694
    # tenure blocks were nearly declared as harvest areas.
    wrong = [f for f in harvest
             if str(f["properties"].get("harp_tier", ""))
             not in ("P1a", "P1d")]
    if wrong:
        kinds = Counter("{} / {}".format(f["properties"].get("harp_tier"),
                                         f["properties"].get("harp_geometry_kind"))
                        for f in wrong)
        log("")
        log("  {} feature(s) in the harvest file are not P1a:".format(
            len(wrong)))
        for k, n in kinds.most_common(5):
            log("    {:>6}  {}".format(n, k))
        log("  Only a block resolved from a mark on the delivery, or one the "
            "producer declared, belongs there. Anything else should be "
            "searched, not declared.")
    return harvest, tenure, search, sizes


def _distribution(sizes: list[float]) -> str:
    if not sizes:
        return "    no areas measured"
    bands = [(0, 1), (1, 10), (10, 50), (50, 200), (200, 500), (500, 2000),
             (2000, 10000), (10000, float("inf"))]
    lines = []
    for lo, hi in bands:
        n = sum(1 for a in sizes if lo < a <= hi)
        if not n:
            continue
        label = ">{:,.0f}".format(lo) if hi == float("inf") else \
            "{:,.0f} - {:,.0f}".format(lo, hi)
        lines.append("    {:>16} ha  {:>6}  {}".format(
            label, n, "#" * min(40, max(1, round(n / len(sizes) * 40)))))
    return "\n".join(lines)


def _window(month: str) -> tuple:
    """The first and last day of a YYYY-MM."""
    from datetime import date as _d, timedelta as _td
    try:
        y, m = (int(x) for x in month.split("-")[:2])
        first = _d(y, m, 1)
        last = _d(y + (m == 12), (m % 12) + 1, 1) - _td(days=1)
        return first.isoformat(), last.isoformat()
    except (ValueError, TypeError):
        return "", ""


def _first(sorted_items: dict, kind: str) -> str:
    """The first file of a kind in the drop, if there is one."""
    for item in sorted_items.get(kind) or []:
        path = getattr(item, "path", "")
        if path and os.path.isfile(path):
            return path
    return ""


def _delivery_file(sorted_items: dict) -> str:
    """The delivery record out of the drop, for the library to keep."""
    return _first(sorted_items, "delivery_record")


def _detect_and_join(cfg, month, start, end, harvest, tenure, search, stamp,
                     api_base, written, say, stage_cb) -> dict:
    """Union, submit, wait, join back, write the month.

    Returns what happened, including where it stopped. A run that could not
    reach detection and one that reached it and found nothing are different
    outcomes and must not look the same.
    """
    from collections import Counter as _C
    from datetime import date as _d

    say("\n" + "=" * 66)
    say("5  UNION")
    say("=" * 66)
    stage_cb("union", "running")
    submit = tenure + search
    if not submit:
        stage_cb("union", "empty", "nothing to submit")
        say("No tenure blocks and no search areas. Everything resolved to a "
            "harvest area, so there is nothing to look for.")
        return {"stopped_at": "union", "why": "nothing needed searching",
                "merged": harvest}
    try:
        feat = detect_stage.union(submit, log=say)
    except RuntimeError as exc:
        stage_cb("union", "failed")
        say(str(exc))
        return {"stopped_at": "union", "why": str(exc), "merged": []}

    union_path = _write(
        "{}/search-union-{}.geojson".format(cfg.paths.outbox, stamp),
        "harp_search_union", [feat],
        {"window": [start, end],
         "note": feat["properties"]["harp_note"]})
    written.append(union_path)
    parts = len(feat["geometry"].get("coordinates") or [])
    stage_cb("union", "done", "{} part(s)".format(parts))
    say("  {}".format(union_path))

    say("\n" + "=" * 66)
    say("6  DETECTION")
    say("=" * 66)
    say("{} to {}".format(start, end))
    stage_cb("detect", "running", "{} to {}".format(start[5:], end[5:]))
    try:
        feats, raw, summary = detection_api.run(
            union_path, start, end, cfg.paths.outbox,
            base=api_base or detection_api.DEFAULT_BASE, log=say)
    except detection_api.DetectionError as exc:
        first = str(exc).splitlines()[0]
        # An empty return is an answer about coverage or the window. A
        # refusal is a fault. Both stop the run and they are not the same
        # thing, so the lamp and the summary say which.
        empty = "returned nothing" in first
        stage_cb("detect", "empty" if empty else "failed", first[:26])
        for k in ("enrich", "write", "validate", "stage"):
            stage_cb(k, "skipped")
        say("")
        say(str(exc))
        say("")
        say("Nothing has been declared for {}. The search areas are written "
            "and can be resubmitted without re-resolving:".format(month))
        say("  harp detect --month {}".format(month))
        return {"stopped_at": "detection",
                "why": ("the service returned nothing for this area and "
                        "window" if empty else first),
                "merged": []}

    det_path = _write(
        "{}/detections-{}.geojson".format(cfg.paths.outbox, stamp),
        "harp_detections", feats,
        {"job": summary["job"], "window": [start, end],
         "raw": os.path.basename(summary["raw"])})
    written.extend([summary["raw"], det_path])
    stage_cb("detect", "done", "{:,}".format(len(feats)))

    say("\n" + "=" * 66)
    say("7  JOIN BACK")
    say("=" * 66)
    stage_cb("enrich", "running")
    dets = detect_stage.read_detections(det_path, log=say)
    b, c, report = detect_stage.enrich(
        tenure, search, dets, _d.fromisoformat(start), _d.fromisoformat(end),
        log=say)
    merged = detect_stage.merge(harvest, b, c)
    stage_cb("enrich", "done", "{:,}".format(len(b) + len(c)))

    month_path = _write(
        "{}/harvest-{}.geojson".format(cfg.paths.outbox, month),
        "harp_harvest", merged,
        {"window": [start, end], "features": len(merged),
         "job": summary["job"],
         "note": ("One month of harvest areas. harp_traceability says how "
                  "each was reached; they are not equally strong.")})
    written.append(month_path)
    stage_cb("write", "done", "{:,}".format(len(merged)))

    trace = _C(f["properties"].get("harp_traceability", "?") for f in merged)
    tiers = _C(f["properties"].get("harp_tier", "?") for f in merged)
    say("")
    say("{:,} harvest area(s) for {}".format(len(merged), month))
    for t, n in sorted(tiers.items()):
        say("  {:<6}{:>8,}".format(t, n))
    say("  " + ", ".join("{:,} {}".format(n, k)
                         for k, n in trace.most_common()))
    say("  {}".format(month_path))

    return {"stopped_at": "month written", "merged": merged,
            "detections": len(feats),
            "traceability": dict(trace)}


def _write(path: str, name: str, feats: list[dict], extra: dict) -> str:
    return io.write_json(path, {
        "type": "FeatureCollection", "name": name,
        "metadata": {"generated": datetime.now().isoformat(timespec="seconds"),
                     "features": len(feats), "schema": SCHEMA, **extra},
        "features": feats})


def run(cfg, folder: str, *, month: str = "", private_marks_dir: str = "",
        register: str = "", mills_csv: str = "", alias_override: str = "",
        max_block_ha: float = MAX_BLOCK_HA, radius_km: float = 150.0,
        fetch_geometry: bool = True, unique: bool = True,
        detect: bool = True, stage: bool = True,
        api_base: str = "", limit: int = 0,
        log=print, on_stage=None) -> dict:
    """The whole month, from the client's drop to a staged library month.

    Sort, resolve, search areas, split, union, detect, join back, validate,
    clean, stage. One run, one log.

    Detection used to be a separate command. It is not optional in any
    meaningful sense - without it the output is a pile of search areas nobody
    can declare - so making it a second step invited runs that looked
    finished and were not. `detect=False` still stops after the split, but
    that is now the exception rather than the default.

    `on_stage(name, state, note)` is called as each stage starts and ends, so
    a caller showing progress does not have to wait for the whole run to
    return before it can say where it is. Called with 'running' then 'done';
    the names are sort, resolve, search, split.
    """
    stage_cb = on_stage or (lambda *_a, **_k: None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_rec = manifest.Run(cfg, "run", {"folder": folder})
    written, lines = [], []

    def say(msg=""):
        log(msg)
        lines.append(str(msg))

    # ---- 1. sort the drop ----------------------------------------------
    say("=" * 66)
    say("1  SORTING THE DROP")
    say("=" * 66)
    stage_cb("sort", "running")
    sorted_items = package.sort_package(folder)
    if not sorted_items:
        say("nothing readable in {}".format(folder))
        return {"ok": False}
    say(package.describe(sorted_items))

    jobs = sorted_items.get("job_list") or []
    if not jobs:
        say("\nNo supply list in that folder. A supply list is recognised by "
            "a SOURCEID column and no LOADID.")
        return {"ok": False}
    if len(jobs) > 1:
        say("\n{} supply lists found; using {}".format(
            len(jobs), os.path.basename(jobs[0].path)))
    supply = jobs[0].path

    # Anything the drop already contains is used from there. Naming a file on
    # the command line that is sitting in the folder being read is a step
    # nobody should have to remember.
    if not register:
        register = _first(sorted_items, "supplier_register")
        if register:
            say("  supplier register found in the drop")
    if not mills_csv:
        mills_csv = _first(sorted_items, "mill_locations")
        if mills_csv:
            say("  mill locations found in the drop")
    lot_list = _first(sorted_items, "lot_list")
    if lot_list:
        say("  a lot list is here too - `harp lot` will use it")
    declared_files = [getattr(i, "path", "")
                      for i in (sorted_items.get("producer_geodata") or [])
                      if getattr(i, "path", "")]
    if declared_files:
        say("  {} file(s) of producer-declared harvest areas".format(
            len(declared_files)))

    marks_dir = private_marks_dir or (
        folder if sorted_items.get("private_marks") else "")
    if marks_dir:
        say("\nprivate mark extracts found - R5b will run")

    # ---- 2. resolve -----------------------------------------------------
    stage_cb("sort", "done", "{} file(s)".format(
        sum(len(v) for v in sorted_items.values())))
    stage_cb("resolve", "running")
    say("\n" + "=" * 66)
    say("2  RESOLVING")
    say("=" * 66)
    records = [identify.identify(r) for r in identify.load(supply)]
    say("{} source(s) from {}".format(len(records), os.path.basename(supply)))
    say("columns mapped: " + identify.describe_mapping())
    if unique:
        before = len(records)
        records = identify.dedupe(records)
        if before != len(records):
            say("{} rows -> {} distinct identifiers".format(before,
                                                            len(records)))
    if limit:
        records = records[:limit]

    from .cache import Cache
    from .sources import hbs
    store = Cache("{}/cache".format(cfg.paths.staging))
    client = hbs.Client(cache=store)

    registry = None
    if marks_dir and os.path.isdir(marks_dir):
        registry = private_marks.Registry(
            marks_dir, cache_dir="{}/cache/bcparcel".format(cfg.paths.staging),
            log=lambda *_: None)
        try:
            n = registry.build()
            say("private mark registry: {} marks indexed".format(n))
        except private_marks.NotInstalled as exc:
            say(str(exc))
            registry = None

    results = []
    for i, rec in enumerate(records, 1):
        res = router.resolve(rec, hbs_client=client,
                             fetch_geometry=fetch_geometry, registry=registry)
        results.append(res)
        if i % 25 == 0 or i == len(records):
            tiers = Counter(r.tier.value for r in results)
            say("  {:>4}/{}  {}".format(i, len(records), dict(sorted(
                tiers.items()))))
            stage_cb("resolve", "running", "{}/{}".format(i, len(records)))

    run_rec.rows_in = len(records)
    run_rec.rows_out = sum(1 for r in results if r.resolved)
    for r in results:
        if not r.resolved:
            run_rec.reject({"source_id": r.source_id,
                            "identifier": r.identifier},
                           r.unresolved_reason or "unresolved",
                           supplier=r.supplier_name)

    written.append(io.write_csv_dicts(
        "{}/resolution-{}.csv".format(cfg.paths.outbox, stamp),
        [r.row() for r in results]))

    collection, report = assemble.assemble(results)
    for line in assemble.summary(report).splitlines():
        say("  " + line)

    # ---- 3. search areas -------------------------------------------------
    #
    # Built here rather than by a separate script. A source that resolved to a
    # harvest area does not get one; a supplier whose sources did not is given
    # the best bounded region available to them.
    stage_cb("resolve", "done", "{:,} of {:,}".format(
        run_rec.rows_out, len(records)))
    stage_cb("search", "running")
    say("\n" + "=" * 66)
    say("3  SEARCH AREAS")
    say("=" * 66)
    catchment_feats = []
    if register:
        try:
            supplier_rows = catchments.read_sources(register)
        except Exception as exc:
            say("could not read the supplier register: {}".format(exc))
            supplier_rows = []
        if supplier_rows:
            mill_rows = catchments.read_mills(mills_csv) if mills_csv else {}
            if not mill_rows:
                say("no mill locations given - the district route will fall "
                    "back to the mill town in each source identifier")
            ids = catchments.read_source_identifiers(supply)
            # The alias table is what turns a supplier into their own tenure,
            # so a path that quietly misses it costs the best search areas in
            # the layer. Try the likely places and say which was used - an
            # earlier run built no operator tenure at all and gave no hint why.
            from .aliases import AliasTable
            candidates = [
                alias_override,
                "{}/registry/supplier_aliases.csv".format(
                    os.path.dirname(cfg.paths.staging.rstrip("/\\")) or "data"),
                os.path.join("data", "registry", "supplier_aliases.csv"),
                os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "data", "registry",
                    "supplier_aliases.csv"),
            ]
            alias_path = next((c for c in candidates
                               if c and os.path.isfile(c)), "")
            table = AliasTable(alias_path) if alias_path else None
            if table:
                say("alias table: {}".format(alias_path))
                say("  " + table.summary().splitlines()[0])
            else:
                say("no alias table found - no supplier will resolve to their "
                    "own tenure. Looked in:")
                for c in candidates:
                    if c:
                        say("    " + c)
            from . import areas as areas_mod
            areas_path = areas_mod.path_for(cfg)
            stated = areas_mod.load(areas_path)
            if stated:
                say("stated areas: {} supplier(s) placed by hand".format(
                    len(stated)))
            collection_c, summary_c = catchments.build(
                supplier_rows, mill_rows, table, radius_km, 0, ids,
                stated=stated, log=lambda *_: None)
            catchment_feats = collection_c["features"]
            by = Counter(r["method"] for r in summary_c)
            say("{} search area(s) across {} supplier(s)".format(
                len(catchment_feats), len(summary_c)))
            for m, n in by.most_common():
                say("  {:<22}{:>4} supplier(s)".format(m, n))
        else:
            say("no suppliers read from the register")
    else:
        say("no --register given, so no search areas were built")
        say("(a supplier register says which suppliers still need one)")

    # ---- 3b: harvest areas the producer declared -------------------------
    #
    # Read with the rest of the drop rather than after the split, so they
    # flow through the split like everything else and are counted by it.
    # They are finished geometry - the producer is asserting they harvested
    # here, and that assertion is the evidence - so they need no search area
    # and no detection.
    declared = []
    if declared_files:
        stage_cb("declared", "running")
        say("\n" + "=" * 66)
        say("3b  DECLARED HARVEST AREAS")
        say("=" * 66)
        try:
            declared, dec_report = producer_geodata.read(
                declared_files, month=month, log=say)
        except Exception as exc:
            stage_cb("declared", "failed", str(exc)[:22])
            say("could not read them: {}".format(exc))
            declared, dec_report = [], {}
        if declared:
            written.append(_write(
                "{}/declared-areas-{}.geojson".format(cfg.paths.outbox, stamp),
                "harp_declared", declared,
                {"month": month, "files": dec_report.get("files", 0),
                 "note": ("harvest areas the producer declared. Taken at "
                          "their word; nothing checked against a register.")}))
            stage_cb("declared", "done", "{:,} at P1d".format(len(declared)))
            say("")
            say("{:,} will join the harvest areas at P1d".format(len(declared)))
            if not month:
                say("No month given, so every declared area was kept. Give a "
                    "month to keep only those with production in it.")
        else:
            # Files were present but nothing survived the month filter. Not a
            # failure - just nothing of theirs ran in this month.
            stage_cb("declared", "empty", "none in this month")
    else:
        # None in the drop. Greyed rather than left dark, so a month where the
        # producer sent nothing looks different from one where the stage never
        # ran.
        stage_cb("declared", "skipped", "none in the drop")

    # ---- 4. split --------------------------------------------------------
    stage_cb("search", "done", "{:,}".format(len(catchment_feats)))
    stage_cb("split", "running")
    say("\n" + "=" * 66)
    say("4  SPLITTING")
    say("=" * 66)
    everything = (list(collection["features"]) + list(catchment_feats)
                  + list(declared))
    harvest, tenure, search, sizes = _split(everything, max_block_ha, log=say)

    say("\nsize of everything classed as a block or parcel:")
    say(_distribution(sizes))

    note = ("Geometry repeats where two suppliers share an area - each "
            "carries its own copy, so a detection can be attributed to "
            "whoever supplied the fibre. Read harp_geometry_kind for what "
            "each polygon is.")
    for name, fs, why in (
            ("harvest-areas", harvest,
             "resolved from an identifier - finished"),
            ("tenure-blocks", tenure,
             "real blocks, wrong scope - awaiting confirmation by detection"),
            ("search-areas", search,
             "places to look - detection has to find the harvest")):
        written.append(_write(
            "{}/{}-{}.geojson".format(cfg.paths.outbox, name, stamp),
            "harp_" + name.replace("-", "_"), fs,
            {"note": note, "purpose": why, "max_block_ha": max_block_ha}))

    say("")
    for name, fs in (("harvest", harvest), ("tenure", tenure),
                     ("search", search)):
        kinds = Counter(f["properties"]["harp_geometry_kind"] for f in fs)
        sup = {f["properties"]["harp_supplier"] for f in fs} - {""}
        area = sum(f["properties"]["harp_area_ha"] or 0 for f in fs)
        say("{:<9}{:>7,} feature(s)  {:>4} supplier(s)  {:>12,.0f} ha".format(
            name, len(fs), len(sup), area))
        for k, n in kinds.most_common():
            say("           {:<20}{:>7,}".format(k, n))

    stage_cb("split", "done", "{:,} + {:,}".format(len(harvest), len(tenure)))

    # ---- 5 to 7: the detection round trip --------------------------------
    #
    # Everything from here writes into the same log as everything above. The
    # old arrangement had stages 1 to 4 in run-*.txt and everything after on
    # stdout, so a run that stopped at detection looked identical on disk to
    # one that never tried.
    outcome = {"stopped_at": "split", "why": ""}
    merged = []
    if not month:
        for k in ("union", "detect", "enrich", "write"):
            stage_cb(k, "skipped", "no window")
        say("\n" + "=" * 66)
        say("STOPPED AFTER THE SPLIT")
        say("=" * 66)
        say("No month given, so nothing was submitted for detection.")
        say("The tenure blocks and search areas are places to look, not "
            "answers. Nothing here should be declared.")
        outcome["why"] = "no month given"
    elif not detect:
        for k in ("union", "detect", "enrich", "write"):
            stage_cb(k, "skipped", "asked not to")
        say("\n" + "=" * 66)
        say("STOPPED AFTER THE SPLIT, AS ASKED")
        say("=" * 66)
        outcome["why"] = "detection turned off"
    else:
        start, end = _window(month)
        if not start:
            outcome.update(stopped_at="union", why="bad month: " + month)
            say("\n--month wants YYYY-MM, got '{}'".format(month))
        else:
            outcome = _detect_and_join(
                cfg, month, start, end, harvest, tenure, search, stamp,
                api_base, written, say, stage_cb)
            merged = outcome.pop("merged", [])

    # ---- 8 to 11: project, validate, clean, stage -------------------------
    if merged and stage:
        # The EUDR view is built before validation, because validation checks
        # those four fields and nothing else. A projection rather than a
        # rename: the month keeps every harp_ field, and this is derived from
        # it, so the record of how a producer name was arrived at survives.
        say("\n" + "=" * 66)
        say("8  EUDR FIELDS")
        say("=" * 66)
        merged, view_report = eudr_schema.add(merged, log=say)
        outcome["eudr_missing"] = view_report.get("missing", {})
        say("")
        say("Added alongside the existing fields, not in place of them. The "
            "validator reads only the four; everything else rides through and "
            "is what a production lot is resolved against later.")
        # Rewrite the month now it carries them, so what is on disk is what
        # goes into the library.
        written.append(_write(
            "{}/harvest-{}.geojson".format(cfg.paths.outbox, month),
            "harp_harvest", merged,
            {"month": month, "features": len(merged),
             "note": ("One month of harvest areas, carrying both the EUDR "
                      "fields and the pipeline's own. Strip to the four with "
                      "`harp deliver` before sending it anywhere.")}))

        stage_cb("validate", "running")
        opts = library_stage.settings(cfg)
        try:
            built = library_stage.build(
                opts["path"], month, merged,
                _delivery_file(sorted_items), opts,
                source_files=[os.path.basename(w) for w in written[-2:]],
                log=say)
            outcome["library_state"] = built["state"]
            stage_cb("validate", "done" if built["state"] == "pending"
                     else "empty",
                     "{} finding(s)".format(
                         built["manifest"]["findings_remaining"])
                     if built["state"] != "pending" else "clean")
            stage_cb("stage", "done" if built["state"] == "pending"
                     else "empty", built["state"])
            outcome["stopped_at"] = "staged"
        except RuntimeError as exc:
            # Most likely the EUDR libraries are not installed. That is a
            # setup problem, not a bad month, and the collection is already
            # written - so say which and carry on.
            stage_cb("validate", "failed", str(exc)[:24])
            stage_cb("stage", "skipped")
            say("")
            say(str(exc))
            say("The month is written but not staged. Install the libraries "
                "and run `harp library build --month {}`.".format(month))
            outcome["library_state"] = "not staged"
    elif merged:
        stage_cb("validate", "skipped", "asked not to")
        stage_cb("stage", "skipped", "asked not to")

    # ---- done -----------------------------------------------------------
    for w in written:
        run_rec.output(w)
    row = run_rec.finish()

    log_path = "{}/run-{}.txt".format(cfg.paths.outbox, stamp)
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        written.append(log_path)
    except OSError:
        pass

    say("\n" + "=" * 66)
    say("run {}".format(row["run_id"]))
    for w in written:
        say("  " + w)
    if run_rec.rejects:
        say("  {} unresolved -> {}".format(len(run_rec.rejects),
                                           cfg.paths.rejects))
        say("  that file is a client question list, not errors")

    say("")
    say("this run got as far as: {}".format(outcome["stopped_at"]))
    if outcome.get("why"):
        say("  {}".format(outcome["why"]))
    if outcome.get("library_state") == "pending":
        say("  {} is waiting on approval:".format(month))
        say("    harp library promote --month {} --who <you>".format(month))
    elif outcome.get("library_state") == "quarantine":
        say("  {} is in quarantine with findings outstanding. It needs hands "
            "on it, not another pass.".format(month))

    return {"ok": True, "stamp": stamp, "written": written,
            "harvest": len(harvest), "tenure": len(tenure),
            "search": len(search), "detections": outcome.get("detections", 0),
            "month_features": len(merged),
            "blocks": len(harvest) + len(tenure), "regions": len(search),
            "resolved": run_rec.rows_out, "sources": len(results),
            "stopped_at": outcome["stopped_at"],
            "why": outcome.get("why", ""),
            "lot_list": lot_list,
            "deliveries": _delivery_file(sorted_items),
            "library_state": outcome.get("library_state", ""),
            "traceability": outcome.get("traceability", {})}
