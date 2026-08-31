#!/usr/bin/env python3
"""Does a Washington Forest Practices permit number give us a harvest boundary?

    python tools/fpa_probe.py --supplier "WILLIS ENTERPRISES"
    python tools/fpa_probe.py --permits "2815123, NW-FPA-26-8878"
    python tools/fpa_probe.py --supplier "WILLIS ENTERPRISES" --out willis_out

WHY
---
Willis Enterprises has offered a monthly list of logs purchased, with volume
and the Washington State Forest Practices permit number for each. If that
number resolves to a harvest boundary, it is the Washington equivalent of a
timber mark: a specific harvest tied to a specific delivery, rather than
everywhere the company might have cut.

That is the difference between a declaration built on eleven matched
applications and one built on the actual ground their logs came off.

WHAT THIS CHECKS
    1  the permit number is found in the state register
    2  it carries a polygon, not just a record
    3  the polygon has an area, a date and a status worth reading

Run it against a supplier's own applications first, before asking anyone to
compile months of data on the strength of an assumption.

TWO THINGS THAT WILL NOT RESOLVE
    Some applications are never digitised - they sit on a "Not Digitized"
    layer with no geometry at all. Of one large landowner's 7,970 permits,
    fewer than a quarter carried a polygon.

    The register keeps ten years. Anything older is gone.

Neither is a reason not to ask. A permit that resolves is worth far more than
a company footprint, and one that does not is a known gap rather than a silent
one.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone

import requests

ROOT = ("https://gis.dnr.wa.gov/site2/rest/services/Public_Forest_Practices"
        "/WADNR_PUBLIC_FP_FPA/MapServer")
PARTY_LAYERS = {11: "landowner", 12: "operator", 13: "timberowner"}
TIMEOUT = 120

S = requests.Session()
S.headers.update({"User-Agent": "NGIS-HARP-fpa-probe/1.0"})


def get(url: str, params: dict) -> dict:
    r = S.get(url, params={**params, "f": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"].get("message", str(d["error"])))
    return d


def sql(v) -> str:
    return str(v).replace("'", "''")


def when(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts) / 1000,
                                      timezone.utc).date().isoformat()
    except Exception:
        return ""


def polygon_layers() -> list[dict]:
    """Every layer that could hold a harvest boundary, and its fields."""
    out = []
    meta = get(ROOT, {})
    for lyr in meta.get("layers", []):
        lid = lyr.get("id")
        if lid in PARTY_LAYERS:
            continue
        try:
            info = get("{}/{}".format(ROOT, lid), {})
        except Exception:
            continue
        names = {f["name"].upper(): f["name"] for f in info.get("fields", [])}
        if "FP_ID" not in names:
            continue
        out.append({"id": lid, "name": info.get("name", ""),
                    "geometry": info.get("geometryType", ""),
                    "fields": names})
    return out


def permits_for(supplier: str, log=print) -> list[str]:
    """Every permit number filed under this company name, all three roles."""
    ids: set[str] = set()
    for layer, role in PARTY_LAYERS.items():
        cursor, guard = None, 0
        base = "UPPER(ORG) LIKE '%{}%'".format(sql(supplier).upper())
        while guard < 40:
            guard += 1
            where = base if cursor is None else \
                "({}) AND OBJECTID > {}".format(base, cursor)
            try:
                d = get("{}/{}/query".format(ROOT, layer),
                        {"where": where, "outFields": "FP_ID,OBJECTID,ORG",
                         "returnGeometry": "false",
                         "orderByFields": "OBJECTID",
                         "resultRecordCount": 1000})
            except Exception as exc:
                log("  {}: {}".format(role, str(exc)[:70]))
                break
            feats = d.get("features") or []
            if not feats:
                break
            oids = []
            for f in feats:
                a = f["attributes"]
                if a.get("FP_ID") not in (None, ""):
                    ids.add(str(a["FP_ID"]))
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
    return sorted(ids)


def resolve(permit: str, layers: list[dict]) -> list[dict]:
    """Look for this permit on every layer. Returns what was found, where."""
    found = []
    for lyr in layers:
        f = lyr["fields"]
        want = [f[k] for k in ("FP_ID", "TIMHARV_RPT_AREA", "RECEIVED_DT",
                               "EXPIRATION_DT", "DECISION", "CLASSIFICATION",
                               "REGION_NM", "HARVEST_UNIT_NO")
                if k in f]
        try:
            d = get("{}/{}/query".format(ROOT, lyr["id"]),
                    {"where": "{} = '{}'".format(f["FP_ID"], sql(permit)),
                     "outFields": ",".join(want), "returnGeometry": "true",
                     "outSR": 4326, "resultRecordCount": 50})
        except Exception:
            continue
        for feat in d.get("features") or []:
            a = feat.get("attributes", {})
            rings = (feat.get("geometry") or {}).get("rings")
            found.append({
                "permit": permit, "layer": lyr["name"], "layer_id": lyr["id"],
                "has_geometry": bool(rings),
                "parts": len(rings) if rings else 0,
                "acres": a.get(f.get("TIMHARV_RPT_AREA", ""), ""),
                "received": when(a.get(f.get("RECEIVED_DT", ""))),
                "expires": when(a.get(f.get("EXPIRATION_DT", ""))),
                "decision": a.get(f.get("DECISION", ""), ""),
                "classification": a.get(f.get("CLASSIFICATION", ""), ""),
                "region": a.get(f.get("REGION_NM", ""), ""),
                "unit": a.get(f.get("HARVEST_UNIT_NO", ""), ""),
                "geometry": ({"type": "MultiPolygon",
                              "coordinates": [[r] for r in rings]}
                             if rings else None),
            })
        time.sleep(0.1)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--supplier", help="company name — finds their permits "
                                       "first, then resolves them")
    ap.add_argument("--permits", help="comma separated permit numbers")
    ap.add_argument("--sample", type=int, default=8,
                    help="how many permits to resolve when using --supplier")
    ap.add_argument("--out", default="fpa_probe_out")
    args = ap.parse_args()
    if not (args.supplier or args.permits):
        ap.error("give --supplier or --permits")

    print("Washington Forest Practices — does a permit number give geometry?\n")
    layers = polygon_layers()
    print("{} layer(s) carry FP_ID:".format(len(layers)))
    for l in layers:
        print("   {:<4}{:<44}{}".format(
            l["id"], l["name"][:44],
            "area field" if "TIMHARV_RPT_AREA" in l["fields"] else "no area"))

    if args.permits:
        permits = [p.strip() for p in args.permits.split(",") if p.strip()]
        print("\n{} permit(s) given".format(len(permits)))
    else:
        print("\nfinding permits filed under '{}'…".format(args.supplier))
        permits = permits_for(args.supplier)
        print("{} permit number(s) found".format(len(permits)))
        if not permits:
            print("\nNothing under that name. Try a shorter form of it.")
            return
        print("  {}".format(", ".join(permits[:12])
                            + (" …" if len(permits) > 12 else "")))
        permits = permits[:args.sample]
        print("\nresolving the first {}…".format(len(permits)))

    rows, with_geom = [], 0
    print("\n{:<20}{:<40}{:>9}{:>12}{:>12}".format(
        "PERMIT", "FOUND ON", "POLYGON", "ACRES", "RECEIVED"))
    print("-" * 95)
    for p in permits:
        hits = resolve(p, layers)
        if not hits:
            print("{:<20}{:<40}".format(p[:20], "not found on any layer"))
            rows.append({"permit": p, "layer": "", "has_geometry": False,
                         "acres": "", "received": "", "decision": "",
                         "note": "not found"})
            continue
        for h in hits:
            if h["has_geometry"]:
                with_geom += 1
            print("{:<20}{:<40}{:>9}{:>12}{:>12}".format(
                h["permit"][:20], h["layer"][:40],
                "yes" if h["has_geometry"] else "no",
                "{:,.0f}".format(float(h["acres"])) if h["acres"] else "",
                h["received"]))
            r = {k: v for k, v in h.items() if k != "geometry"}
            rows.append(r)

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if rows:
        with open(os.path.join(args.out, "fpa_probe_{}.csv".format(stamp)),
                  "w", encoding="utf-8-sig", newline="") as fh:
            cols = sorted({k for r in rows for k in r})
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    feats = []
    for p in permits:
        for h in resolve(p, layers):
            if h.get("geometry"):
                feats.append({"type": "Feature", "geometry": h["geometry"],
                              "properties": {k: v for k, v in h.items()
                                             if k != "geometry"}})
                break
    if feats:
        path = os.path.join(args.out, "fpa_permits_{}.geojson".format(stamp))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh)
        print("\n  {}".format(path))

    print("\n" + "-" * 95)
    print("{} of {} permit(s) returned a boundary.".format(
        with_geom, len(permits)))
    if with_geom:
        print("\nA permit number resolves to a harvest boundary. That makes it "
              "the Washington equivalent of a timber mark - a specific harvest "
              "tied to a specific delivery, rather than everywhere the company "
              "might have cut.")
    else:
        print("\nNo boundary came back. Either these permits are not "
              "digitised, or the number is not what the register calls FP_ID. "
              "Worth checking one by hand at "
              "fortress.wa.gov/dnr/protection/fparssearch before concluding.")


if __name__ == "__main__":
    main()
