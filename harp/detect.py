"""The detection round trip - what goes out, and what comes back.

    harp union   --out-dir data/outbox        # one polygon to submit
    harp enrich  detections.geojson --month 2026-07

WHAT GOES OUT
-------------
A single search area covering everything that needs looking at: the tenure
constellations and the catchments, unioned. One polygon rather than thousands,
because the service runs a query per feature and returns everything it finds
inside regardless.

The union is a submission artefact and nothing else. It is never declared, and
the per-supplier geometry it was built from is kept untouched - that is what
makes the return attributable.

WHAT COMES BACK
---------------
Detected harvest polygons and points, carrying a date, an area, and a feature
type. No supplier, no timber mark, no link to anything we sent. Attribution
has to be recovered here.

    geo           the geometry
    date          when the disturbance was first detected
    area_ha       its size
    feature_type  'polygon', or 'point' below about four hectares

HOW ATTRIBUTION IS RECOVERED
----------------------------
Differently for the two inputs, because they mean different things.

**Tenure blocks, P2a.** Registered harvest areas attributable to a supplier,
carrying no reliable date. A detection overlapping one, inside the window,
confirms it was cut and says when - the block is kept and becomes **P2b**. One
with no overlap is dropped: the record stands, but nothing places the harvest
inside the period being declared for.

**Search areas, P3a.** Administrative areas. Nothing places a harvest anywhere
in particular, so here the detection *is* the finding. Each one becomes a
harvest area in its own right, attributed to the supplier whose search area
contains it.

Both end up as dated geometry and they are not the same thing. The tier says
so: P2b is a register polygon with a confirmed date; a detected block inside a
search area is P3b either way, though one inside a marked parcel carries a mark and an owner and one inside a district does not.

DUPLICATES ARE EXPECTED
-----------------------
Where two suppliers' catchments overlap, a detection inside both is attributed
to both. The geometry repeats; the attribution does not. Merging them would
lose which supplier a harvest should be declared against, which is the only
reason any of this is being done.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime

# Kinds that need looking at. Harvest areas resolved from an identifier do not
# - they are already the answer.
TENURE_KINDS = {"cut_block", "tenure_envelope"}
CATCHMENT_KINDS = {"district", "county", "national_forest", "mill_buffer",
                   "oversized_block", "large_parcel"}


# ───────────────────────────── reading ─────────────────────────────────────

def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh)
    return gj.get("features") or []


def _parse_date(v) -> date | None:
    """A date from whatever the service put in the field.

    Seen so far: '2026-07-30 00:00:00 UTC', '2026-07-30T20:34:17Z', and epoch
    milliseconds. Accepting several costs nothing; guessing wrong loses the
    only attribute the return actually carries.
    """
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return datetime.utcfromtimestamp(float(v) / 1000).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip().replace("UTC", "").strip()
    s = s.replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 4].strip(), fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(*(int(x) for x in m.groups()))
        except ValueError:
            return None
    return None


def read_detections(path: str, log=print) -> list[dict]:
    """The service's return, from GeoJSON or from a table of WKT.

    Both shapes have been seen. A CSV export carries the geometry as WKT in a
    `geo` column; a GeoJSON carries it properly. Same fields either way.
    """
    from shapely import wkt as shapely_wkt
    from shapely.geometry import mapping, shape

    out = []
    if path.lower().endswith((".geojson", ".json")):
        for f in _load(path):
            p = f.get("properties") or {}
            out.append({
                "geometry": f.get("geometry"),
                "date": _parse_date(p.get("date") or p.get("detected")
                                    or p.get("alert_date")),
                "area_ha": p.get("area_ha") or p.get("area"),
                "feature_type": p.get("feature_type") or "",
            })
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("geo") or row.get("geometry")
                       or row.get("wkt") or "").strip()
                if not raw:
                    continue
                try:
                    geom = mapping(shapely_wkt.loads(raw))
                except Exception:
                    continue
                out.append({
                    "geometry": geom,
                    "date": _parse_date(row.get("date")),
                    "area_ha": row.get("area_ha"),
                    "feature_type": (row.get("feature_type") or "").strip(),
                })

    undated = sum(1 for d in out if not d["date"])
    kinds = Counter(d["feature_type"] or "unspecified" for d in out)
    log("{:,} detection(s): {}".format(
        len(out), ", ".join("{} {}".format(n, k) for k, n in kinds.most_common())))
    if undated:
        log("  {:,} carry no date and cannot be filtered by window".format(
            undated))
    dated = [d["date"] for d in out if d["date"]]
    if dated:
        log("  detected {} to {}".format(min(dated), max(dated)))
    return out


# ───────────────────────────── the union ───────────────────────────────────

def union(features: list[dict], log=print) -> dict:
    """One polygon covering everything that needs looking at.

    Built from the tenure blocks and the catchments together, so the submitted
    area covers both by construction rather than by someone checking that it
    does.
    """
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    shapes = []
    for f in features:
        try:
            s = shape(f["geometry"])
            if not s.is_valid:
                s = s.buffer(0)
            if not s.is_empty:
                shapes.append(s)
        except Exception:
            continue
    if not shapes:
        raise RuntimeError("nothing to union")

    merged = unary_union(shapes)
    log("{:,} feature(s) unioned into {} part(s)".format(
        len(shapes), len(getattr(merged, "geoms", [merged]))))
    return {"type": "Feature", "geometry": mapping(merged), "properties": {
        "harp_note": ("a submission artefact - the area to search inside. "
                      "Never declare this. Attribution lives in the "
                      "per-supplier files it was built from"),
        "harp_built_from": len(shapes)}}


# ─────────────────────────────── enrich ────────────────────────────────────

def _index(features: list[dict]):
    """Prepared shapes plus an STRtree, or None if shapely is unavailable."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    shapes, keep = [], []
    for f in features:
        try:
            s = shape(f["geometry"])
            if not s.is_valid:
                s = s.buffer(0)
        except Exception:
            continue
        if s.is_empty:
            continue
        shapes.append(s)
        keep.append(f)
    return (STRtree(shapes) if shapes else None), shapes, keep


def enrich(tenure: list[dict], catchments: list[dict], detections: list[dict],
           start: date | None = None, end: date | None = None,
           log=print) -> tuple[list[dict], list[dict], dict]:
    """B-prime and C-prime.

    Tenure blocks are confirmed and dated; catchment detections become blocks
    of their own. Returns (b_prime, c_prime, report).
    """
    from shapely.geometry import shape

    in_window = []
    for d in detections:
        if start or end:
            if not d["date"]:
                continue
            if start and d["date"] < start:
                continue
            if end and d["date"] > end:
                continue
        in_window.append(d)
    log("{:,} of {:,} detection(s) fall in the window".format(
        len(in_window), len(detections)))
    if not in_window:
        return [], [], {"detections": len(detections), "in_window": 0}

    det_shapes = []
    for d in in_window:
        try:
            s = shape(d["geometry"])
            if not s.is_valid:
                s = s.buffer(0)
        except Exception:
            continue
        if not s.is_empty:
            det_shapes.append((s, d))

    # Both inputs are search areas. The detection is what is kept; the area
    # only says whose it was and what else is known about it.
    def attribute(parents, kind, tier, note, log_label):
        """Emit each detection under every area that contains it."""
        if not parents:
            return [], 0
        from shapely.strtree import STRtree
        shapes, feats_in = [], []
        for f in parents:
            try:
                sh = shape(f["geometry"])
                if not sh.is_valid:
                    sh = sh.buffer(0)
            except Exception:
                continue
            if not sh.is_empty:
                shapes.append(sh)
                feats_in.append(f)
        if not shapes:
            return [], 0
        tree = STRtree(shapes)
        out, shared = [], 0
        for ds, d in det_shapes:
            owners = [i for i in tree.query(ds) if shapes[i].intersects(ds)]
            if not owners:
                continue
            if len(owners) > 1:
                shared += 1
            for i in owners:
                # Every area containing this detection gets its own copy.
                # Merging them would lose which supplier the harvest should be
                # declared against, which is the only reason for any of this.
                p = feats_in[i].get("properties") or {}
                out.append({"type": "Feature", "geometry": d["geometry"],
                            "properties": {
                    "harp_supplier": p.get("harp_supplier", ""),
                    "harp_supplier_code": p.get("harp_supplier_code", ""),
                    "harp_jurisdiction": p.get("harp_jurisdiction", ""),
                    "harp_geometry_kind": kind,
                    "harp_method": "change detection within " + log_label,
                    "harp_source_system": "HLS-DIST via NGIS",
                    # Inherited from the area the detection fell in. A tenure
                    # block carries a mark and a holder; a district carries
                    # neither, and the empty fields say so.
                    "harp_key": p.get("harp_key", ""),
                    "harp_key_name": p.get("harp_key_name", ""),
                    "harp_timber_mark": p.get("harp_timber_mark", ""),
                    # Inherited with everything else. A detection inside a
                    # tenure block belongs to whoever held that tenure.
                    "ProducerName": p.get("ProducerName", ""),
                    "harp_producer_number": p.get("harp_producer_number", ""),
                    "harp_producer_source": p.get("harp_producer_source", ""),
                    "harp_district": p.get("harp_district", ""),
                    "harp_parent_kind": p.get("harp_geometry_kind", ""),
                    "harp_parent_area_ha": p.get("harp_area_ha", ""),
                    "harp_area_ha": round(float(d.get("area_ha") or 0), 2),
                    "harp_detected": True,
                    "harp_detected_first": (d["date"].isoformat()
                                            if d["date"] else ""),
                    "harp_detection_type": d.get("feature_type", ""),
                    "harp_tier": tier,
                    "harp_is_envelope": False,
                    "harp_traceability": {"P1c": "direct",
                                          "P2b": "indirect"}.get(
                                              tier, "inferred"),
                    "harp_declared_by_supplier": False,
                    "harp_evidence": note,
                    "harp_note": ("a harvest detected inside this supplier's "
                                  + log_label + ". The area said whose it was; "
                                  "the detection is the harvest."),
                }})
        log("{:,} detection(s) attributed within {:,} {}".format(
            len(out), len(shapes), log_label))
        if shared:
            log("  {:,} fell inside more than one and are attributed to "
                "each".format(shared))
        return out, shared

    # A parcel carries a mark from the client's own delivery record, so a
    # detection inside one is reached from the delivery rather than from a
    # company name. That is a different chain of evidence from a tenure block
    # and it keeps its own tier.
    parcels = [f for f in catchments
               if (f.get("properties") or {}).get("harp_geometry_kind")
               in ("parcel", "large_parcel")]
    areas = [f for f in catchments
             if (f.get("properties") or {}).get("harp_geometry_kind")
             not in ("parcel", "large_parcel")]

    a_prime, _ = attribute(
        parcels, "detected_block", "P1c",
        "detection within the parcel this mark was scaled from",
        "titled parcel(s)")
    b_prime, _ = attribute(
        tenure, "detected_block", "P2b",
        "detection within a registered tenure area",
        "tenure block(s)")
    c_prime, _ = attribute(
        areas, "detected_block", "P3b",
        "detection within a search area",
        "search area(s)")
    # Parcels are the strongest of the three, so they lead.
    b_prime = a_prime + b_prime

    # A detection can fall inside both a tenure block and a wider search area
    # - the block is the better parent, so the P3b copy is dropped where a
    # P2b already covers the same ground for the same supplier.
    seen = {(f["properties"]["harp_supplier"],
             json.dumps(f["geometry"], sort_keys=True)) for f in b_prime}
    before = len(c_prime)
    c_prime = [f for f in c_prime
               if (f["properties"]["harp_supplier"],
                   json.dumps(f["geometry"], sort_keys=True)) not in seen]
    if before != len(c_prime):
        log("{:,} duplicate(s) dropped - already attributed to the same "
            "supplier through a tenure block".format(before - len(c_prime)))

    report = {"detections": len(detections), "in_window": len(in_window),
              "tenure_in": len(tenure), "tenure_detected": len(b_prime),
              "catchments": len(catchments), "detected_blocks": len(c_prime)}
    return b_prime, c_prime, report


def merge(harvest: list[dict], b_prime: list[dict],
          c_prime: list[dict]) -> list[dict]:
    """One collection for the month, with every feature saying what it rests on."""
    out = []
    for f in harvest:
        p = dict(f.get("properties") or {})
        p.setdefault("harp_evidence", "resolved from an identifier")
        p.setdefault("harp_traceability", "direct")
        p.setdefault("harp_detected", False)
        out.append({"type": "Feature", "geometry": f["geometry"],
                    "properties": p})
    out.extend(b_prime)
    out.extend(c_prime)
    return out


def summary(report: dict) -> str:
    lines = ["{:,} detection(s), {:,} in the window".format(
        report.get("detections", 0), report.get("in_window", 0))]
    if report.get("tenure_detected"):
        lines.append("{:,} attributed within {:,} tenure block(s) - these "
                     "carry a mark and a holder".format(
                         report["tenure_detected"], report.get("tenure_in", 0)))
    if report.get("detected_blocks"):
        lines.append("{:,} attributed within {:,} search area(s)".format(
            report["detected_blocks"], report.get("catchments", 0)))
    lines.append("The search areas themselves are not kept. What is declared "
                 "is the ground detection found, not the area it was found in.")
    return "\n".join(lines)
