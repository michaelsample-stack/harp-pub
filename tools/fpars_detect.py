#!/usr/bin/env python3
"""Washington FPA polygons, then DIST detection over them.

An independent test of the whole Washington route: pull the harvest
application polygons matched to Harmac's suppliers, run change detection inside
each, and get back the ground that was actually disturbed.

Two steps, deliberately separate.

    python tools/fpars_detect.py extract --since 2021
    python tools/fpars_detect.py detect  --start 2024-01-01 --end 2026-07-31

**extract** talks only to Washington DNR. It works whether or not Earth Engine
is set up, and writes a GeoJSON you can open and check before spending
anything on detection.

**detect** needs Earth Engine and tracemark-eo. It fails with a plain
explanation if either is missing rather than part way through.

WHY THIS MATTERS
----------------
A Forest Practices Application is permission to cut, not evidence of a cut.
Some are approved and never harvested, some withdrawn, some expired. Matching
Harmac's suppliers by name returned roughly 86,000 hectares of applications
against maybe 50 to 100 hectares of actual harvest behind a month's intake -
so the applications on their own over-declare by orders of magnitude.

Detection is what separates ground that was disturbed from ground that merely
had approval. It is the difference between a defensible declared area and a
list of everywhere a company might have cut.

ONE FPA, ONE ANSWER
-------------------
`polyToChangeDetectionPoly_DIST` dissolves every detection inside each search
polygon into a single feature and carries the join id through. That is the
right behaviour here: an application is one harvest unit, so one polygon in
means one harvest answer out, still attached to its FP_ID.

COST
----
Smaller than it sounds. 86,000 hectares is 860 square kilometres, and the DIST
alerts are pre-computed rather than derived per run. The wait is the export
round trip, not the computation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

ROOT = ("https://gis.dnr.wa.gov/site2/rest/services/Public_Forest_Practices/"
        "WADNR_PUBLIC_FP_FPA/MapServer")

PARTY_LAYERS = {11: "landowner", 12: "operator", 13: "timberowner"}

DEFAULT_TERMS = [
    "WEYERHAEUSER", "SIERRA PACIFIC", "MANKE", "INTERFOR",
    "HERMANN BROS", "WILLIS ENTERPRISES", "ALTA FOREST", "GREEN DIAMOND",
]

# The layer to take geometry from. "All Harvest by Classification" and "All
# Harvest by Decision/Status" return identical sets - they are views of the
# same applications - so either serves, and adding them would double count.
PREFERRED_LAYER = "All Harvest by Classification"

TIMEOUT = 240


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "NGIS-HARP-fpars-detect/1.0"})
    return s


def get(s, url: str, params: dict) -> dict:
    r = s.get(url, params={**params, "f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data


def sql(v) -> str:
    return str(v).replace("'", "''")


def load_pointtopoly(repo_root: str, log=None):
    """Import tracemark-eo's pointtopoly module, whatever shape the repo is in.

    `pointtopoly` is a directory holding `pointtopoly.py` and carrying no
    __init__.py, so a plain `from pointtopoly import ...` resolves to the
    namespace package - the folder - and the functions are not there. The
    module inside has to be reached explicitly.

    Tried in order, because the layout may not stay this way:
        pointtopoly.pointtopoly     the current shape
        pointtopoly                 if it is ever made a real package
        loaded from the file path   last resort, no import machinery involved
    """
    import importlib
    import importlib.util
    log = log or (lambda *_: None)

    repo_root = os.path.abspath(os.path.expanduser(repo_root))
    if not os.path.isdir(repo_root):
        raise RuntimeError("Not a folder: {}".format(repo_root))

    # Check the file is actually there before importing anything. Otherwise a
    # module cached from an earlier attempt answers instead, and a wrong path
    # reports whatever went wrong last time rather than the real problem.
    direct = os.path.join(repo_root, "pointtopoly", "pointtopoly.py")
    if not os.path.isfile(direct):
        raise RuntimeError(
            "No pointtopoly/pointtopoly.py under {}.\n\n"
            "Point this at the tracemark-eo repository root - the folder "
            "holding main.py and the pointtopoly directory - not at the "
            "pointtopoly folder itself.".format(repo_root))

    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    needed = ("polyToChangeDetectionPoly_DIST", "get_harvestable_forest_img",
              "convert_ee_feature_collection_to_geojson")

    errors, missing_deps = [], set()

    def note_missing(exc):
        """Separate a missing third-party package from a wrong path.

        They need different fixes and should not produce the same message.
        """
        if isinstance(exc, ModuleNotFoundError) and exc.name:
            if exc.name.split(".")[0] not in ("pointtopoly",):
                missing_deps.add(exc.name.split(".")[0])

    for name in ("pointtopoly.pointtopoly", "pointtopoly"):
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            note_missing(exc)
            errors.append("{}: {}".format(name, str(exc)[:90]))
            continue
        if all(hasattr(mod, f) for f in needed):
            log("  imported {}".format(name))
            return mod
        errors.append("{}: imported but missing {}".format(
            name, ", ".join(f for f in needed if not hasattr(mod, f))))

    if os.path.isfile(direct):
        try:
            spec = importlib.util.spec_from_file_location("teo_pointtopoly",
                                                          direct)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["teo_pointtopoly"] = mod
            spec.loader.exec_module(mod)
            if all(hasattr(mod, f) for f in needed):
                log("  imported directly from {}".format(direct))
                return mod
            errors.append("file load: missing {}".format(
                ", ".join(f for f in needed if not hasattr(mod, f))))
        except Exception as exc:
            note_missing(exc)
            errors.append("file load: {}".format(str(exc)[:120]))

    if missing_deps:
        raise RuntimeError(
            "tracemark-eo was found at {}, but it needs packages that are not "
            "installed:\n\n    {}\n\n"
            "Install its dependencies:\n"
            "    pip install {}\n\n"
            "Or all of them:\n"
            "    pip install -r \"{}\"".format(
                repo_root, ", ".join(sorted(missing_deps)),
                " ".join(sorted(missing_deps)),
                os.path.join(repo_root, "pointtopoly", "requirements.txt")))

    raise RuntimeError(
        "Could not import tracemark-eo's pointtopoly from {}.\n\n{}\n\n"
        "Expected to find pointtopoly/pointtopoly.py under the repository "
        "root. Note that folder has no __init__.py, so the module inside has "
        "to be reached as pointtopoly.pointtopoly.".format(
            repo_root, "\n".join("  " + e for e in errors)))



# ──────────────────────────────── extract ──────────────────────────────────

def pick_layer(s) -> dict:
    meta = get(s, ROOT, {})
    for lyr in meta.get("layers", []):
        if PREFERRED_LAYER not in lyr.get("name", ""):
            continue
        info = get(s, "{}/{}".format(ROOT, lyr["id"]), {})
        names = [f["name"] for f in info.get("fields", [])]
        return {"id": lyr["id"], "name": lyr["name"], "fields": names}
    raise SystemExit("Could not find the '{}' layer. Check {}?f=pjson".format(
        PREFERRED_LAYER, ROOT))


def fp_ids_for(s, term: str) -> tuple[set[str], dict]:
    """Every FP_ID under this name, across all three party roles.

    Paged on OBJECTID. The service caps a page at a thousand rows and does not
    reliably honour resultOffset, so an offset loop silently under-reports.
    """
    ids: set[str] = set()
    by_role = {}
    for layer, role in PARTY_LAYERS.items():
        here: set[str] = set()
        base = "UPPER(ORG) LIKE '%{}%'".format(sql(term).upper())
        cursor, guard = None, 0
        while guard < 200:
            guard += 1
            where = base if cursor is None else \
                "({}) AND OBJECTID > {}".format(base, cursor)
            try:
                data = get(s, "{}/{}/query".format(ROOT, layer),
                           {"where": where, "outFields": "FP_ID,OBJECTID",
                            "returnGeometry": "false",
                            "orderByFields": "OBJECTID",
                            "resultRecordCount": 1000})
            except Exception:
                break
            feats = data.get("features", [])
            if not feats:
                break
            oids = []
            for f in feats:
                a = f["attributes"]
                if a.get("FP_ID") not in (None, ""):
                    here.add(str(a["FP_ID"]))
                if a.get("OBJECTID") is not None:
                    oids.append(a["OBJECTID"])
            if not oids:
                break
            nxt = max(oids)
            if cursor is not None and nxt <= cursor:
                break
            cursor = nxt
            if len(feats) < 1000:
                break
            time.sleep(0.1)
        by_role[role] = len(here)
        ids |= here
        time.sleep(0.1)
    return ids, by_role


def polygons_for(s, ids: list[str], layer: dict, since: int | None,
                 log=print) -> list[dict]:
    """The harvest polygons for these FP_IDs, as GeoJSON features."""
    out, seen = [], set()
    date_field = "RECEIVED_DT" if "RECEIVED_DT" in layer["fields"] else None
    want = [f for f in ("FP_ID", "TIMHARV_RPT_AREA", "RECEIVED_DT",
                        "EXPIRATION_DT", "DECISION", "CLASSIFICATION",
                        "REGION_NM", "HARVEST_UNIT_NO")
            if f in layer["fields"]]

    for i in range(0, len(ids), 80):
        batch = ids[i:i + 80]
        where = "FP_ID IN ({})".format(
            ",".join("'{}'".format(sql(x)) for x in batch))
        if since and date_field:
            where += " AND {} >= timestamp '{}-01-01 00:00:00'".format(
                date_field, since)
        try:
            r = s.get("{}/{}/query".format(ROOT, layer["id"]),
                      params={"where": where, "outFields": ",".join(want),
                              "returnGeometry": "true", "outSR": 4326,
                              "f": "geojson", "resultRecordCount": 2000},
                      timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log("    batch {} failed: {}".format(i // 80 + 1, str(exc)[:80]))
            continue
        for f in data.get("features", []):
            p = f.get("properties") or {}
            key = "{}|{}".format(p.get("FP_ID"), p.get("HARVEST_UNIT_NO"))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
        time.sleep(0.12)
    return out


def cmd_extract(args) -> int:
    s = _session()
    layer = pick_layer(s)
    print("layer {}  {}".format(layer["id"], layer["name"]))
    print("applications from {} onward\n".format(args.since))

    terms = ([t.strip() for t in args.suppliers.split(",") if t.strip()]
             if args.suppliers else DEFAULT_TERMS)
    os.makedirs(args.out, exist_ok=True)

    all_feats, summary = [], []
    print("{:<22}{:>9}{:>11}{:>12}".format("SUPPLIER", "FP_IDs", "polygons",
                                           "acres"))
    print("-" * 56)
    for term in terms:
        ids, by_role = fp_ids_for(s, term)
        feats = polygons_for(s, sorted(ids), layer, args.since) if ids else []
        acres = 0.0
        for f in feats:
            p = f.setdefault("properties", {})
            p["harp_supplier_term"] = term
            try:
                acres += float(p.get("TIMHARV_RPT_AREA") or 0)
            except (TypeError, ValueError):
                pass
        all_feats.extend(feats)
        summary.append({"term": term, "fp_ids": len(ids),
                        "polygons": len(feats), "acres": round(acres, 1),
                        **by_role})
        print("{:<22}{:>9}{:>11}{:>12,.0f}".format(term[:22], len(ids),
                                                   len(feats), acres))

    total_ac = sum(r["acres"] for r in summary)
    print("-" * 56)
    print("{:<22}{:>9}{:>11}{:>12,.0f}".format(
        "TOTAL", sum(r["fp_ids"] for r in summary), len(all_feats), total_ac))
    print("\n{:,.0f} acres  ·  {:,.0f} hectares".format(
        total_ac, total_ac * 0.404686))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(args.out, "fpars_polygons_{}.geojson".format(stamp))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection",
                   "name": "fpars_applications",
                   "metadata": {
                       "layer": layer["name"], "since": args.since,
                       "generated": datetime.now().isoformat(timespec="seconds"),
                       "note": ("Forest Practices Applications matched by "
                                "company name. Permission to cut, not "
                                "evidence of a cut - run detection before "
                                "treating any of this as harvested."),
                       "by_supplier": summary},
                   "features": all_feats}, fh)
    print("\n  {}".format(path))
    print("\nThese are applications, not harvests. Next:")
    print("  python tools/fpars_detect.py detect --polygons {} \\".format(
        os.path.basename(path)))
    print("         --start 2024-01-01 --end {}".format(
        datetime.now().strftime("%Y-%m-%d")))
    return 0


# ──────────────────────────────── detect ───────────────────────────────────

def cmd_detect(args) -> int:
    """Run tracemark-eo's DIST detection over the extracted polygons."""
    try:
        import ee
    except ImportError:
        sys.exit("earthengine-api is not installed.\n"
                 "  pip install earthengine-api\n"
                 "You will also need a GCP project with Earth Engine enabled.")

    try:
        ptp = load_pointtopoly(args.tracemark_eo, log=print)
    except RuntimeError as exc:
        sys.exit(str(exc))
    convert_ee_feature_collection_to_geojson = \
        ptp.convert_ee_feature_collection_to_geojson
    get_harvestable_forest_img = ptp.get_harvestable_forest_img
    polyToChangeDetectionPoly_DIST = ptp.polyToChangeDetectionPoly_DIST

    path = args.polygons
    if not os.path.isfile(path):
        # allow a bare filename from the output folder
        alt = os.path.join(args.out, os.path.basename(path))
        if os.path.isfile(alt):
            path = alt
        else:
            sys.exit("Not found: {}".format(args.polygons))
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh)
    feats = gj.get("features") or []
    if not feats:
        sys.exit("No features in that file.")

    if args.limit:
        feats = feats[:args.limit]
    print("{} application polygon(s)".format(len(feats)))
    print("window {} to {}".format(args.start, args.end))
    print("confidence >= {}\n".format(args.confidence))

    print("initialising Earth Engine…")
    try:
        ee.Initialize(project=args.project) if args.project else ee.Initialize()
    except Exception:
        print("  not authenticated — opening a browser")
        ee.Authenticate()
        ee.Initialize(project=args.project) if args.project else ee.Initialize()

    # FP_ID is the join back to the application. Every output feature carries
    # it, so a detected harvest can be traced to the application it sits in.
    for i, f in enumerate(feats):
        f.setdefault("properties", {})["joinID"] = str(
            f["properties"].get("FP_ID") or i)

    fc = ee.FeatureCollection({"type": "FeatureCollection", "features": feats})

    print("building the forest mask…")
    harvestable = get_harvestable_forest_img()
    # Dynamic World crops layer, as the production scripts use it: ground under
    # crops is not a forest harvest.
    now = ee.Date(args.end)
    dw = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
          .filterDate(now.advance(-8, "months"), now)
          .select("crops").mean())
    dw_mask = dw.lt(0.5).rename("dw")

    print("running detection…")
    detected = polyToChangeDetectionPoly_DIST(
        args.start, args.end, fc, "joinID", harvestable, dw_mask,
        args.confidence, "harp-fpars-test")

    print("collecting…")
    try:
        out_json = convert_ee_feature_collection_to_geojson(detected)
    except Exception as exc:
        sys.exit("Collecting the result failed: {}\n"
                 "If this is a size or timeout error the set is too large for "
                 "a direct fetch and needs an export task instead.".format(exc))

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(args.out, "fpars_detected_{}.geojson".format(stamp))
    result = json.loads(out_json)
    result.setdefault("metadata", {}).update({
        "source": os.path.basename(path),
        "window": [args.start, args.end],
        "confidence": args.confidence,
        "note": ("Disturbance detected inside each Forest Practices "
                 "Application. One feature per application, joined on FP_ID."),
    })
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(result, fh)

    got = result.get("features") or []
    alerted = [f for f in got
               if str((f.get("properties") or {}).get("alert")).lower() == "true"]
    print("\n{} feature(s) returned".format(len(got)))
    print("{} with disturbance detected".format(len(alerted)))
    print("{} applications with nothing detected — approved but, on this "
          "evidence, not cut in the window".format(len(got) - len(alerted)))
    print("\n  {}".format(dest))
    print("\nThat difference is the point of the exercise: an application is "
          "permission, and only some of it was acted on.")
    return 0


# ──────────────────────────────── main ─────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="pull the application polygons")
    e.add_argument("--suppliers", help="comma separated name terms")
    e.add_argument("--since", type=int, default=2021,
                   help="earliest application year (default 2021 — detection "
                        "cannot verify anything earlier)")
    e.add_argument("--out", default="fpars_out")
    e.set_defaults(fn=cmd_extract)

    d = sub.add_parser("detect", help="run DIST detection over them")
    d.add_argument("--polygons", required=True,
                   help="the geojson written by extract")
    d.add_argument("--start", required=True, help="YYYY-MM-DD")
    d.add_argument("--end", required=True, help="YYYY-MM-DD")
    d.add_argument("--confidence", type=int, default=6,
                   help="minimum DIST status, 1-8 (default 6)")
    d.add_argument("--project", help="Earth Engine / GCP project id")
    d.add_argument("--tracemark-eo", default="../tracemark-eo",
                   help="path to the tracemark-eo repository")
    d.add_argument("--limit", type=int,
                   help="only the first N polygons — worth using for a first run")
    d.add_argument("--out", default="fpars_out")
    d.set_defaults(fn=cmd_detect)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
