"""A Washington Forest Practices permit number, to the ground it covers.

    from harp.sources import fpars
    feats, report = fpars.geometry(["2618447", "2943196"], log=log)

WHAT AN FPA NUMBER IS
---------------------
Washington's equivalent of a timber mark. A supplier files a Forest Practices
Application before harvesting, the state approves it, and the approved unit is
published with its boundary. A permit on a supplier's declaration is therefore
a specific harvest rather than everywhere that company might have cut.

That it resolves at all was worth checking before building on it. Of the
twenty-one permits on one supplier's monthly declaration, eighteen came back
with boundaries - fifty-four polygons, every one carrying an area, a median of
twenty-three acres. The three that did not are filed with a tribal nation's own
department rather than with the state and are not in this register.

ONE LAYER, NOT EIGHT
--------------------
The service publishes the same features under eight layers - active harvest,
all harvest, by decision, by classification - which are filters over one set
rather than eight sets. Querying all of them returns each polygon several
times: eighteen permits came back as two hundred and eighteen rows.

`FPA - All Harvest by Classification` is the one to read. It carried every
polygon the others did, all with areas.

**Not Digitized is not a layer to read.** It records applications that exist
without geometry, and it answered for forty-seven of those two hundred and
eighteen rows. A hit there is a permit the state has no boundary for, which is
worth knowing and is not a harvest area.

A PERMIT COVERS SEVERAL UNITS
-----------------------------
One permit returned eleven polygons between one and ninety-two acres. That is
the same shape as a timber mark covering several cut blocks: real harvest
geometry, more than fed any one delivery, and narrowed by detection to what
moved in the month.
"""

from __future__ import annotations

import time
from collections import Counter

import requests

ROOT = ("https://gis.dnr.wa.gov/site2/rest/services/Public_Forest_Practices"
        "/WADNR_PUBLIC_FP_FPA/MapServer")

# The layer that carries every approved harvest unit with its boundary. The
# others are filters over the same features, and reading more than one returns
# the same polygon several times.
LAYER = 6
LAYER_NAME = "FPA - All Harvest by Classification"

# Applications the state holds without geometry. Never read for boundaries;
# a hit here means the permit exists and has no published shape.
NOT_DIGITISED = 16

FIELD = "FP_ID"
TIMEOUT = 120
BATCH = 40          # permits per IN clause
RETRIES = 3
BACKOFF = (2, 8, 20)

_S = requests.Session()
_S.headers.update({"User-Agent": "harp-fpars/1.0"})


class ServiceError(RuntimeError):
    pass


def _get(url: str, params: dict, log=None) -> dict:
    last = None
    for attempt in range(RETRIES):
        try:
            r = _S.get(url, params={**params, "f": "json"}, timeout=TIMEOUT)
            if r.status_code == 429 or r.status_code >= 500:
                raise ServiceError("HTTP {}".format(r.status_code))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise ServiceError(data["error"].get("message",
                                                     str(data["error"])))
            return data
        except Exception as exc:
            last = exc
            if attempt == RETRIES - 1:
                break
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise ServiceError(str(last).splitlines()[0] if last else "unknown")


def _quote(v: str) -> str:
    return "'{}'".format(str(v).replace("'", "''").strip().upper())


def geometry(permits, log=print) -> tuple:
    """Harvest boundaries for these permits.

    Returns (features, report). A permit that resolves gives one feature per
    approved unit. A permit that does not is named, because a declared permit
    with no boundary is a finding rather than a silent absence.
    """
    wanted = [str(p).strip().upper() for p in permits if str(p or "").strip()]
    wanted = sorted(set(wanted))
    if not wanted:
        return [], {"asked": 0, "found": 0}

    log("{} permit(s) to look up".format(len(wanted)))
    feats, seen = [], set()
    for start in range(0, len(wanted), BATCH):
        chunk = wanted[start:start + BATCH]
        where = "{} IN ({})".format(FIELD, ",".join(_quote(p) for p in chunk))
        try:
            data = _get("{}/{}/query".format(ROOT, LAYER), {
                "where": where, "outFields": "*", "returnGeometry": "true",
                "outSR": 4326, "resultRecordCount": 2000})
        except ServiceError as exc:
            log("  {} permit(s) failed: {}".format(
                len(chunk), str(exc).splitlines()[0][:90]))
            continue
        for f in data.get("features") or []:
            attrs = f.get("attributes") or {}
            geom = _to_geojson(f.get("geometry") or {})
            if geom is None:
                continue
            pid = str(attrs.get(FIELD) or "").strip().upper()
            # The same unit can be returned twice where a permit was amended.
            key = (pid, str(attrs.get("OBJECTID") or ""))
            if key in seen:
                continue
            seen.add(key)
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": _props(pid, attrs)})

    found = {f["properties"]["harp_key"] for f in feats}
    missing = [p for p in wanted if p not in found]
    per = Counter(f["properties"]["harp_key"] for f in feats)

    log("  {} of {} permit(s) resolved, {} unit(s) between them".format(
        len(found), len(wanted), len(feats)))
    if per:
        log("  a permit covers {:.1f} unit(s) on average, up to {}".format(
            sum(per.values()) / len(per), max(per.values())))
    if missing:
        # Named rather than counted. A declared permit the state has no
        # boundary for is a question for the supplier, and it is the kind of
        # gap that disappears if it is only ever totalled.
        log("  no boundary for: {}".format(", ".join(missing[:8])
                                           + (" ..." if len(missing) > 8
                                              else "")))
    return feats, {"asked": len(wanted), "found": len(found),
                   "units": len(feats), "missing": missing}


def _props(permit: str, attrs: dict) -> dict:
    """What a unit carries through the pipeline."""
    acres = attrs.get("ACRES") or attrs.get("HARVEST_ACRES") or 0
    return {
        "harp_key": permit,
        "harp_key_name": "Washington FPA {}".format(permit),
        "harp_registry": "WA DNR Forest Practices",
        "harp_source_system": LAYER_NAME,
        "harp_jurisdiction": "WA",
        "ProducerCountry": "US",
        "harp_county": str(attrs.get("COUNTY_NAME")
                           or attrs.get("COUNTY") or "").strip(),
        "harp_permit_status": str(attrs.get("DECISION")
                                  or attrs.get("STATUS") or "").strip(),
        "harp_permit_received": _date(attrs.get("RECEIVED_DATE")
                                      or attrs.get("DATE_RECEIVED")),
        # The state's own acreage, kept beside the measured area so a
        # difference between the two is visible rather than resolved silently.
        "harp_stated_acres": acres,
        "harp_declared_by_supplier": True,
        "harp_basis": ("a Washington Forest Practices unit, from a permit "
                       "number the supplier declared"),
    }


def _date(v) -> str:
    if not v:
        return ""
    try:
        # The service returns epoch milliseconds.
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(v) / 1000,
                                      timezone.utc).date().isoformat()
    except Exception:
        return str(v)[:10]


def _to_geojson(geom: dict):
    """An Esri geometry as GeoJSON. Rings only - these are all polygons."""
    rings = geom.get("rings")
    if not rings:
        return None
    parts = [[[float(x), float(y)] for x, y, *_ in ring] for ring in rings
             if ring and len(ring) >= 4]
    if not parts:
        return None
    if len(parts) == 1:
        return {"type": "Polygon", "coordinates": parts}
    # Esri does not distinguish outer rings from holes in the response, and
    # guessing by winding order gets it wrong on donut units. A MultiPolygon
    # of separate parts overstates area where a hole exists; the measured
    # area is recomputed downstream and the stated acreage sits beside it.
    return {"type": "MultiPolygon", "coordinates": [[p] for p in parts]}
