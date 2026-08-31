#!/usr/bin/env python3
"""How much Washington ground is behind each supplier's name?

The probe established that Harmac's Washington suppliers appear in the FPARS
party tables and that the join works. What it did not establish is how much
land that actually amounts to - only how many applications. A count cannot tell
you whether a declaration over-declares; hectares can.

Harmac took roughly 5,000 BDT from Washington in July. Whether the matched
ground is fifty thousand hectares or five hundred thousand is the whole
question, and it is a decision for Nathan rather than something to engineer
around. This produces the number that lets him make it.

    python tools/fpars_acreage.py
    python tools/fpars_acreage.py --suppliers "WEYERHAEUSER,SIERRA PACIFIC"
    python tools/fpars_acreage.py --since 2021 --out fpars_out

WHAT IT DOES
    for each supplier term
        find matching ORG values in the three party tables
        collect the FP_IDs
        sum the area of the harvest polygons carrying those FP_IDs

Field names are discovered from the service rather than assumed - the layer
schema has not been confirmed and a wrong guess would fail silently.

TWO THINGS THE OUTPUT IS NOT
    Not a declaration. This is everywhere a company might have cut, not where
    the fibre came from. Same class of claim as a BC operating envelope.

    Not final. An application is permission, not a harvest. Change detection
    within the delivery window is what separates disturbed ground from merely
    approved ground.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime

import requests

ROOT = ("https://gis.dnr.wa.gov/site2/rest/services/Public_Forest_Practices/"
        "WADNR_PUBLIC_FP_FPA/MapServer")

# The three roles a company can hold on an application. A mill that owns no
# land still appears as timber owner - which is how Interfor showed up with
# 146 applications and zero as landowner, and why all three must be queried.
PARTY_LAYERS = {11: "landowner", 12: "operator", 13: "timberowner"}

# Harmac's Washington suppliers, from the register
DEFAULT_TERMS = [
    "WEYERHAEUSER", "SIERRA PACIFIC", "MANKE", "INTERFOR",
    "HERMANN BROS", "WILLIS ENTERPRISES", "ALTA FOREST", "GREEN DIAMOND",
]

TIMEOUT = 180
S = requests.Session()
S.headers.update({"User-Agent": "NGIS-HARP-fpars/1.0"})


def get(url: str, params: dict) -> dict:
    r = S.get(url, params={**params, "f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data


def sql(v) -> str:
    return str(v).replace("'", "''")


# ─────────────────────────── discovering the service ───────────────────────

def layers() -> list[dict]:
    return get(ROOT, {}).get("layers", []) + get(ROOT, {}).get("tables", [])


def fields(layer: int) -> list[dict]:
    try:
        return get("{}/{}".format(ROOT, layer), {}).get("fields", [])
    except Exception:
        return []


def find_polygon_layers() -> list[dict]:
    """EVERY polygon layer carrying FP_ID, not just one.

    The service splits harvest applications across several layers by state -
    active, approved, expired - and an application sits in exactly one of them.
    Picking a single layer silently loses everything in the others: an earlier
    version chose "FPA - Expired Last 10 Years" and returned zero polygons for
    every supplier, which read as an answer rather than as a mistake.
    """
    out = []
    for lyr in layers():
        lid = lyr.get("id")
        if lid in PARTY_LAYERS:
            continue
        try:
            info = get("{}/{}".format(ROOT, lid), {})
        except Exception:
            continue
        if info.get("geometryType") != "esriGeometryPolygon":
            continue
        names = {f["name"].upper(): f["name"] for f in info.get("fields", [])}
        if not any("FP_ID" in n for n in names):
            continue
        out.append({
            "id": lid,
            "name": info.get("name", ""),
            "max_records": info.get("maxRecordCount", 1000),
            "fp_id": names.get("FP_ID")
                     or next((names[n] for n in names if "FP_ID" in n), None),
            # Only a reported-area field is usable. SHAPE.AREA is in the
            # layer's projected units, not acres, and summing it produced
            # 264 billion acres before this was separated out.
            "area": next((names[n] for n in names
                          if "RPT_AREA" in n or "ACRE" in n), None),
            "shape_area": next((names[n] for n in names
                                if n == "SHAPE.AREA"), None),
            "status": next((names[n] for n in names if "STATUS" in n), None),
            "date": next((names[n] for n in names
                          if "RECEIV" in n or "APPROV" in n or "DATE" in n), None),
            "oid": names.get("OBJECTID") or next(
                (names[n] for n in names if "OBJECTID" in n), None),
            "all": sorted(names.values()),
        })
    return out


# ────────────────────────────── the query ──────────────────────────────────

def org_matches(term: str, layer: int, org_field: str = "ORG") -> list[str]:
    """Distinct ORG values containing this term. Raw - curation comes later."""
    try:
        data = get("{}/{}/query".format(ROOT, layer), {
            "where": "UPPER({}) LIKE '%{}%'".format(org_field, sql(term).upper()),
            "outFields": org_field, "returnDistinctValues": "true",
            "returnGeometry": "false", "resultRecordCount": 500})
    except Exception:
        return []
    out = []
    for f in data.get("features", []):
        v = (f["attributes"].get(org_field) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def fp_ids(term: str, layer: int, org_field: str = "ORG",
           log=None) -> tuple[set[str], str]:
    """Every FP_ID on this layer whose ORG contains the term.

    Paged on OBJECTID rather than resultOffset. The service caps a page at a
    thousand rows and does not reliably honour an offset, so an offset-based
    loop stops early and under-reports - Weyerhaeuser came back as 1,275
    against a known 7,974 before this was changed.

    Returns (ids, error). An empty result and a failed query are different
    things and must not look the same.
    """
    ids: set[str] = set()
    where_base = "UPPER({}) LIKE '%{}%'".format(org_field, sql(term).upper())
    cursor, guard = None, 0
    while guard < 200:
        guard += 1
        where = where_base if cursor is None else \
            "({}) AND OBJECTID > {}".format(where_base, cursor)
        try:
            data = get("{}/{}/query".format(ROOT, layer), {
                "where": where, "outFields": "FP_ID,OBJECTID",
                "returnGeometry": "false", "orderByFields": "OBJECTID",
                "resultRecordCount": 1000})
        except Exception as exc:
            return ids, str(exc)[:120]
        feats = data.get("features", [])
        if not feats:
            break
        oids = []
        for f in feats:
            a = f["attributes"]
            v = a.get("FP_ID")
            if v not in (None, ""):
                ids.add(str(v))
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
    return ids, ""


def _date_clause(field: str, since: int) -> list[str]:
    """Date syntaxes worth trying, in order.

    ArcGIS accepts different forms depending on the backing store, and a
    rejected WHERE returns an error rather than an empty result - so each is
    tried until one is accepted rather than assuming.
    """
    return [
        "{} >= timestamp '{}-01-01 00:00:00'".format(field, since),
        "{} >= DATE '{}-01-01'".format(field, since),
        "EXTRACT(YEAR FROM {}) >= {}".format(field, since),
    ]


def rows_for(ids: list[str], layer: dict, since: int | None,
             chosen_date: str | None = None) -> tuple[dict, str, str]:
    """FP_ID and area for every polygon on this layer matching these ids.

    Summed client side rather than through outStatistics. The service accepts
    an outStatistics parameter and then ignores it, returning ordinary features
    - so the aggregate came back empty and read as zero hectares rather than as
    a failure. Anything that can be checked by reading the response should be.

    Returns ({fp_id: total_area}, chosen_date_form, error).
    """
    field_id, field_area = layer["fp_id"], layer.get("area")
    out: dict[str, float] = defaultdict(float)
    date_forms = (_date_clause(layer["date"], since)
                  if (since and layer.get("date")) else [""])
    if chosen_date is not None:
        date_forms = [chosen_date]
    last_err = ""

    for i in range(0, len(ids), 120):
        batch = ids[i:i + 120]
        vals = ",".join("'{}'".format(sql(x)) for x in batch)
        base = "{} IN ({})".format(field_id, vals)
        fields_wanted = field_id + ("," + field_area if field_area else "")

        got = False
        for form in date_forms:
            where = base + (" AND " + form if form else "")
            try:
                data = get("{}/{}/query".format(ROOT, layer["id"]),
                           {"where": where, "outFields": fields_wanted,
                            "returnGeometry": "false",
                            "resultRecordCount": 2000})
            except Exception as exc:
                last_err = str(exc)[:100]
                continue
            for f in data.get("features", []):
                a = f["attributes"]
                fid = a.get(field_id)
                if fid in (None, ""):
                    continue
                try:
                    area = float(a.get(field_area) or 0) if field_area else 0.0
                except (TypeError, ValueError):
                    area = 0.0
                out[str(fid)] += area
            chosen_date = form
            got = True
            break
        if not got and last_err:
            return dict(out), chosen_date, last_err
        time.sleep(0.12)
    return dict(out), chosen_date, ""


def acreage(ids: set[str], layers_info: list[dict], since: int | None,
            log=None) -> dict:
    """Area of the harvest polygons carrying these FP_IDs.

    THE LAYERS OVERLAP. "All Harvest by Classification", "Active Harvest by
    Classification" and "All Harvest by Decision/Status" are views of the same
    applications, not disjoint sets - so adding them together double counts.
    Each is measured separately and the largest is reported, with the rest
    shown so the overlap is visible rather than assumed.
    """
    out = {"polygons": 0, "acres": 0.0, "ids_matched": 0,
           "ids_queried": len(ids), "errors": [], "by_layer": {},
           "layer_used": ""}
    if not ids:
        return out
    log = log or (lambda *_: None)
    id_list = sorted(ids)

    best = None
    for lyr in layers_info:
        if not lyr.get("fp_id"):
            continue
        areas, _form, err = rows_for(id_list, lyr, since)
        if err and "layer {}: {}".format(lyr["id"], err) not in out["errors"]:
            out["errors"].append("layer {}: {}".format(lyr["id"], err))
        if not areas:
            continue
        usable = bool(lyr.get("area"))
        total = sum(areas.values()) if usable else 0.0
        rec = {"ids": len(areas), "acres": round(total, 1),
               "area_usable": usable}
        out["by_layer"][lyr["name"]] = rec
        log("      {:<40} {:>6} ids  {:>13}".format(
            lyr["name"][:40], len(areas),
            "{:,.0f} ac".format(total) if usable else "no area field"))

        # Only a layer with a reported-area field can be the answer. A layer
        # of non-digitised records has the most ids and no usable geometry -
        # choosing on id count alone picked exactly that and reported an area
        # in the billions.
        if usable and (best is None or total > best[1]["acres"]):
            best = (lyr, rec)

    if best:
        out["layer_used"] = best[0]["name"]
        out["polygons"] = best[1]["ids"]
        out["acres"] = best[1]["acres"]
        out["ids_matched"] = best[1]["ids"]
    # ids present on any layer, including those without geometry - the gap
    # between this and ids_matched is applications with no polygon at all
    out["ids_anywhere"] = max([v["ids"] for v in out["by_layer"].values()]
                              or [0])
    out["hectares"] = round(out["acres"] * 0.404686, 1)
    return out


# ─────────────────────── what the fields can filter ───────────────────────

# Fields worth knowing the values of before building a filter. Each could cut
# the matched set before detection runs, and detection is a job you wait on -
# so anything that can be filtered by attribute first is worth having.
FILTER_FIELDS = ["DECISION", "CLASSIFICATION", "TIMHARV_FP_TY_LABEL_NM",
                 "CUTTING_OR_REMOVING_TIMBER_FLG", "FP_JURISDICT_NM",
                 "REGION_NM", "TEN_YEAR_PLAN_FLG", "ALTERNATE_PLAN_FLG"]


def field_values(layer: dict, field: str, since: int | None,
                 ids: set[str] | None = None) -> list[tuple[str, int, float]]:
    """Distinct values of a field, with how many applications and how many
    acres sit behind each.

    Counted over the supplier's own matched applications where those are
    known, because the value distribution across all of Washington says
    nothing about whether a filter helps us.
    """
    field_id, field_area = layer["fp_id"], layer.get("area")
    if field not in layer.get("all", []):
        return []

    where = "1=1"
    if since and layer.get("date"):
        where = _date_clause(layer["date"], since)[0]

    tally: dict[str, list] = defaultdict(lambda: [0, 0.0])
    want = ",".join(x for x in (field, field_id, field_area) if x)
    cursor, guard = None, 0
    keep = {str(i) for i in ids} if ids else None

    while guard < 400:
        guard += 1
        w = where if cursor is None else "({}) AND OBJECTID > {}".format(
            where, cursor)
        try:
            data = get("{}/{}/query".format(ROOT, layer["id"]),
                       {"where": w, "outFields": want + ",OBJECTID",
                        "returnGeometry": "false",
                        "orderByFields": "OBJECTID",
                        "resultRecordCount": 2000})
        except Exception:
            break
        feats = data.get("features", [])
        if not feats:
            break
        oids = []
        for f in feats:
            a = f["attributes"]
            if a.get("OBJECTID") is not None:
                oids.append(a["OBJECTID"])
            if keep is not None and str(a.get(field_id)) not in keep:
                continue
            v = a.get(field)
            v = "(null)" if v in (None, "") else str(v).strip()
            try:
                area = float(a.get(field_area) or 0) if field_area else 0.0
            except (TypeError, ValueError):
                area = 0.0
            tally[v][0] += 1
            tally[v][1] += area
        if not oids:
            break
        nxt = max(oids)
        if cursor is not None and nxt <= cursor:
            break
        cursor = nxt
        if len(feats) < 2000:
            break
        time.sleep(0.1)

    return sorted(((k, v[0], round(v[1], 1)) for k, v in tally.items()),
                  key=lambda r: -r[2])


def probe_filters(layer: dict, since: int | None, ids: set[str] | None,
                  out_dir: str, stamp: str) -> None:
    """What each field could remove, before detection is run at all.

    Detection is a job you submit and collect later. Anything an attribute
    filter can take out first is time not spent waiting, so this is worth
    knowing before the expensive step rather than after.
    """
    print("\n" + "=" * 74)
    print("WHAT THE ATTRIBUTES COULD FILTER")
    print("=" * 74)
    print("Layer {}  ·  {}{}".format(
        layer["id"], layer["name"],
        "  ·  restricted to the matched applications" if ids else ""))

    rows = []
    for field in FILTER_FIELDS:
        vals = field_values(layer, field, since, ids)
        if not vals or len(vals) > 40:
            continue
        total_ac = sum(v[2] for v in vals) or 1
        print("\n{}".format(field))
        print("  {:<44}{:>8}{:>13}{:>8}".format("value", "apps", "acres", "share"))
        for v, n, ac in vals[:14]:
            print("  {:<44}{:>8}{:>13,.0f}{:>7.0f}%".format(
                v[:44], n, ac, ac / total_ac * 100))
            rows.append({"field": field, "value": v, "applications": n,
                         "acres": ac, "share_pct": round(ac / total_ac * 100, 1)})
        if len(vals) > 14:
            print("  … and {} more values".format(len(vals) - 14))

    if rows:
        path = os.path.join(out_dir, "fpars_filters_{}.csv".format(stamp))
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print("\n  {}".format(path))
    print("\nA value holding a large share of the acreage is where a filter "
          "would bite. Anything removed here is not sent to detection.")


# ──────────────────────────────── main ─────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--suppliers", help="comma separated name terms")
    ap.add_argument("--since", type=int, default=2021,
                    help="earliest application year (default 2021 - detection "
                         "cannot verify anything earlier)")
    ap.add_argument("--out", default="fpars_out")
    ap.add_argument("--describe-only", action="store_true",
                    help="report the service schema and stop")
    ap.add_argument("--layer", type=int,
                    help="restrict to one polygon layer id")
    ap.add_argument("--verbose", action="store_true",
                    help="show the per-layer breakdown as it goes")
    ap.add_argument("--filters", action="store_true",
                    help="report what the DECISION, CLASSIFICATION and flag "
                         "fields could filter out before detection runs")
    args = ap.parse_args()

    terms = ([t.strip() for t in args.suppliers.split(",") if t.strip()]
             if args.suppliers else DEFAULT_TERMS)
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 74)
    print("FPARS — harvest polygon layers")
    print("=" * 74)
    infos = find_polygon_layers()
    if not infos:
        sys.exit("No polygon layer carrying FP_ID was found. The service may "
                 "have changed; check {}?f=pjson".format(ROOT))
    if args.layer is not None:
        infos = [i for i in infos if i["id"] == args.layer]
    print("{:<4}{:<40}{:<22}{:<14}".format("id", "layer", "area field", "date field"))
    print("-" * 74)
    for i in infos:
        print("{:<4}{:<40}{:<22}{:<14}".format(
            i["id"], i["name"][:40],
            i.get("area") or "none — not usable for area",
            i.get("date") or "—"))
    print("-" * 74)
    print("An application sits in exactly one of these, depending on its "
          "state. All are queried.")
    if args.describe_only:
        for i in infos:
            print("\n{} {}".format(i["id"], i["name"]))
            print("  " + ", ".join(i["all"][:26]))
        return

    print("\n" + "=" * 74)
    print("PER SUPPLIER   ({} terms, applications from {} onward)".format(
        len(terms), args.since))
    print("=" * 74)
    print("\nThe polygon layers overlap - they are views of the same "
          "applications. Each is measured separately and the largest is "
          "reported; adding them would double count.")
    print("\n{:<22}{:>9}{:>9}{:>9}{:>11}{:>11}".format(
        "SUPPLIER TERM", "ORGs", "FP_IDs", "matched", "acres", "hectares"))
    print("-" * 74)

    rows, orgs_seen = [], []
    matched_ids: set[str] = set()
    for term in terms:
        ids: set[str] = set()
        role_ids, errs = {}, []
        orgs = set()
        for layer, role in PARTY_LAYERS.items():
            these, err = fp_ids(term, layer)
            if err:
                errs.append("{}: {}".format(role, err))
            role_ids[role] = len(these)
            ids |= these
            for o in org_matches(term, layer):
                orgs.add(o)
            time.sleep(0.15)

        res = acreage(ids, infos, args.since,
                      log=print if args.verbose else None) if ids else {}
        errs += res.get("errors", [])
        matched_ids |= ids
        rows.append({
            "term": term, "org_names": len(orgs), "fp_ids": len(ids),
            "as_landowner": role_ids.get("landowner", 0),
            "as_operator": role_ids.get("operator", 0),
            "as_timberowner": role_ids.get("timberowner", 0),
            "polygons": res.get("polygons", 0),
            "ids_anywhere": res.get("ids_anywhere", 0),
            "layer_used": res.get("layer_used", ""),
            "acres": res.get("acres", 0),
            "hectares": res.get("hectares", 0),
            "ids_matched": res.get("ids_matched", 0),
            "note": ("; ".join(errs)[:200] if errs else
                     "no FP_IDs under this name" if not ids else
                     "only {} of {} ids matched a polygon".format(
                         res.get("ids_matched", 0), len(ids))
                     if res.get("ids_matched", 0) < len(ids) * 0.5 else ""),
        })
        for o in sorted(orgs):
            orgs_seen.append({"term": term, "org": o})
        print("{:<22}{:>9}{:>9}{:>9}{:>11,.0f}{:>11,.0f}".format(
            term[:22], len(orgs), len(ids), res.get("ids_matched", 0),
            res.get("acres", 0), res.get("hectares", 0)))

    total_ha = sum(r["hectares"] for r in rows)
    print("-" * 74)
    print("{:<22}{:>9}{:>9}{:>9}{:>11,.0f}{:>11,.0f}".format(
        "TOTAL", "", sum(r["fp_ids"] for r in rows),
        sum(r["ids_matched"] for r in rows),
        sum(r["acres"] for r in rows), total_ha))

    if args.filters:
        usable = [i for i in infos if i.get("area")]
        target = next((i for i in usable
                       if "All Harvest by Classification" in i["name"]),
                      usable[0] if usable else None)
        if target:
            probe_filters(target, args.since, matched_ids or None,
                          args.out, stamp)

    p1 = os.path.join(args.out, "fpars_acreage_{}.csv".format(stamp))
    with open(p1, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    p2 = os.path.join(args.out, "fpars_org_names_{}.csv".format(stamp))
    with open(p2, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["term", "org"])
        w.writeheader(); w.writerows(orgs_seen)

    bad = [r for r in rows if r["fp_ids"] and not r["acres"]]
    if bad:
        print("\n{} supplier(s) matched FP_IDs but no polygons at all. That is "
              "a join failure, not a result:".format(len(bad)))
        for r in bad:
            print("  {:<24} {}".format(r["term"][:24], r["note"][:80]))

    no_geom = sum(max(0, r["ids_anywhere"] - r["ids_matched"]) for r in rows)
    print("\n{:,.0f} hectares across {} supplier terms, from applications "
          "{} onward.".format(total_ha, len(terms), args.since))
    if no_geom:
        print("{:,} further applications appear on a layer with no usable "
              "geometry - mostly 'Not Digitized'. Their area is unknown, so "
              "the figure above is a floor.".format(no_geom))
    print("Harmac took roughly 5,000 BDT from Washington in July. That ratio "
          "is the question for Nathan.")
    print("\nThese are raw substring matches. The ORG names file is the "
          "starting point for the alias table - curate before using any of "
          "this in a declaration.")
    print("\n  {}\n  {}".format(p1, p2))


if __name__ == "__main__":
    main()
