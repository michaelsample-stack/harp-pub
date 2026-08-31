"""BC Forest Tenure (FTEN) cutblock acquisition.

Resolves harvest area polygons from BC's open forest tenure data, given a
tenure holder's client number.

    Source  : WHSE_FOREST_TENURE.FTEN_CUT_BLOCK_POLY_SVW        (layer 340)
              WHSE_ADMIN_BOUNDARIES.ADM_NR_DISTRICTS_SPG        (layer 748)
    Service : ArcGIS REST, mpcm/bcgwpub MapServer
    Licence : Open Government Licence - British Columbia

No API key. No authentication.

TWO SERVICE QUIRKS, both verified against the live endpoint
-----------------------------------------------------------
1. `resultOffset` is silently ignored on groupBy/statistics queries - every
   page returns identical rows. We never use it; we page on a key instead.

2. Filtering CLIENT_NAME by substring does not work the way you expect.
   '%HARMAC%' returns nothing because the tenure is registered to Nanaimo
   Forest Products Ltd. Always filter on CLIENT_NUMBER.

THE COMPLETION RULE
-------------------
Presence in FTEN does not mean timber was cut. A block can be ACTIVE with a
future PLANNED_HARVEST_DATE. `completion_predicate()` holds our definition of
"harvested" in one place so it can be argued about, changed, and cited.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

ROOT = "https://delivery.maps.gov.bc.ca/arcgis/rest/services/mpcm/bcgwpub/MapServer"
BLOCKS = ROOT + "/340/query"
DISTRICTS = ROOT + "/748/query"

PAGE = 1000
TIMEOUT = 180
ATTRIBUTION = ("Contains information licensed under the "
               "Open Government Licence - British Columbia.")

# Fields we carry through. Everything else is dropped at normalisation but kept
# here so the raw record is preserved for audit.
CORE_FIELDS = [
    "CUT_BLOCK_SKEY", "TIMBER_MARK", "CUT_BLOCK_ID", "CUT_BLOCK_FOREST_FILE_ID",
    "OPENING_ID", "CLIENT_NUMBER", "CLIENT_LOCATION_CODE", "CLIENT_NAME",
    "HARVEST_AUTH_FOREST_FILE_ID", "HARVEST_AUTH_CUTTING_PERMIT_ID",
    "LIFE_CYCLE_STATUS_CODE", "BLOCK_STATUS_CODE", "BLOCK_STATUS_DATE",
    "DISTURBANCE_START_DATE", "DISTURBANCE_END_DATE", "PLANNED_HARVEST_DATE",
    "RETIREMENT_DATE", "ADMIN_DISTRICT_NAME", "GEOGRAPHIC_DISTRICT_NAME",
    "FEATURE_AREA", "PLANNED_GROSS_BLOCK_AREA", "PLANNED_NET_BLOCK_AREA",
    "FEATURE_CLASS_SKEY", "CUT_REGULATION_CODE",
]


# ──────────────────────────────── service ──────────────────────────────────

class ServiceError(RuntimeError):
    pass


def _post(url: str, params: dict) -> dict:
    r = requests.post(url, data=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise ServiceError(data["error"].get("message", str(data["error"])))
    return data


def count(where: str) -> int:
    """How many blocks match. Cheap - always call this before a pull."""
    return _post(BLOCKS, {"where": where, "returnCountOnly": "true",
                          "f": "json"}).get("count", 0)


def region_map() -> dict[str, list[str]]:
    """{NR region name: [district names]} from the province's own hierarchy.

    Not a guess about what 'coastal' means - this is the Ministry's grouping.
    Coastal is West Coast plus South Coast.
    """
    data = _post(DISTRICTS, {
        "where": "1=1",
        "outFields": "REGION_ORG_UNIT_NAME,DISTRICT_NAME",
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "orderByFields": "REGION_ORG_UNIT_NAME,DISTRICT_NAME",
        "resultRecordCount": PAGE,
        "f": "json",
    })
    out: dict[str, list[str]] = {}
    for f in data.get("features", []):
        a = f["attributes"]
        region, district = a.get("REGION_ORG_UNIT_NAME"), a.get("DISTRICT_NAME")
        if region and district:
            out.setdefault(region, []).append(district)
    return out


def clients(where: str = "1=1", log=print) -> list[dict]:
    """Every tenure holder with a cutblock, plus a block count.

    Keyset pagination on CLIENT_NUMBER, because resultOffset does not work here.
    """
    fields = ["CLIENT_NUMBER", "CLIENT_NAME", "CLIENT_LOCATION_CODE"]
    import json as _json

    seen, rows, cursor = set(), [], None
    while True:
        w = where if cursor is None else f"({where}) AND CLIENT_NUMBER >= '{cursor}'"
        data = _post(BLOCKS, {
            "where": w,
            "groupByFieldsForStatistics": ",".join(fields),
            "outStatistics": _json.dumps([{
                "statisticType": "count",
                "onStatisticField": "OBJECTID",
                "outStatisticFieldName": "BLOCK_COUNT",
            }]),
            "orderByFields": "CLIENT_NUMBER",
            "returnGeometry": "false",
            "resultRecordCount": PAGE,
            "f": "json",
        })
        page = [f["attributes"] for f in data.get("features", [])]
        if not page:
            break

        fresh = 0
        for row in page:
            key = tuple(row.get(f) for f in fields)
            if key not in seen:
                seen.add(key)
                rows.append(row)
                fresh += 1
        log(f"  {len(page)} rows, {fresh} new (total {len(rows)})")

        if len(page) < PAGE or fresh == 0:
            break
        cursor = page[-1]["CLIENT_NUMBER"]
        time.sleep(0.3)

    rows.sort(key=lambda r: -(r.get("BLOCK_COUNT") or 0))
    return rows


def features(where: str, log=print) -> list[dict]:
    """Blocks as GeoJSON features in WGS84.

    Keyset pagination on OBJECTID, and it stops if the cursor fails to advance
    rather than looping forever.
    """
    out, cursor, page_no = [], None, 0
    while True:
        w = where if cursor is None else f"({where}) AND OBJECTID > {cursor}"
        data = _post(BLOCKS, {
            "where": w,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "orderByFields": "OBJECTID",
            "resultRecordCount": PAGE,
            "f": "geojson",
        })
        feats = data.get("features", [])
        if not feats:
            break
        out.extend(feats)
        page_no += 1
        log(f"  page {page_no}: {len(feats)} features (total {len(out)})")

        ids = [f.get("properties", {}).get("OBJECTID") for f in feats]
        ids = [i for i in ids if i is not None]
        if not ids:
            log("  ! no OBJECTID in response - stopping")
            break
        nxt = max(ids)
        if cursor is not None and nxt <= cursor:
            log("  ! cursor stalled - stopping")
            break
        cursor = nxt

        if len(feats) < PAGE:
            break
        time.sleep(0.3)
    return out


# ──────────────────────────── the completion rule ──────────────────────────

@dataclass
class CompletionRule:
    """Our working definition of 'this block was harvested'.

    Default: the disturbance has both a start and an end date, and the start is
    within the window. An end date is the strongest available evidence that the
    activity actually happened rather than merely being approved.

    `require_start` can be relaxed - roughly a fifth of records have a null
    DISTURBANCE_START_DATE, so requiring it drops real harvests. Decide
    deliberately and write down why.
    """
    start_after: str | None = None          # 'YYYY-MM-DD'
    start_before: str | None = None
    require_end_date: bool = True
    require_start: bool = True
    exclude_retired: bool = False

    def sql(self) -> list[str]:
        parts: list[str] = []
        if self.require_end_date:
            parts.append("DISTURBANCE_END_DATE IS NOT NULL")
        if self.require_start:
            parts.append("DISTURBANCE_START_DATE IS NOT NULL")
        if self.start_after:
            parts.append(f"DISTURBANCE_START_DATE > DATE '{self.start_after}'")
        if self.start_before:
            parts.append(f"DISTURBANCE_START_DATE < DATE '{self.start_before}'")
        if self.exclude_retired:
            parts.append("LIFE_CYCLE_STATUS_CODE <> 'RETIRED'")
        return parts

    def describe(self) -> str:
        return " AND ".join(self.sql()) or "no completion filter"


# ──────────────────────────────── query build ──────────────────────────────

def build_where(
    client_numbers: Iterable[str] | None = None,
    client_locations: dict[str, Iterable[str]] | None = None,
    districts: Iterable[str] | None = None,
    district_field: str = "GEOGRAPHIC_DISTRICT_NAME",
    timber_marks: Iterable[str] | None = None,
    rule: CompletionRule | None = None,
) -> str:
    parts: list[str] = []

    if client_locations:
        chunks = [
            "(CLIENT_NUMBER = '{}' AND CLIENT_LOCATION_CODE IN ({}))".format(
                num, ",".join(f"'{l}'" for l in sorted(set(locs))))
            for num, locs in client_locations.items()
        ]
        parts.append("(" + " OR ".join(chunks) + ")")
    elif client_numbers:
        nums = ",".join(f"'{n}'" for n in sorted(set(client_numbers)))
        parts.append(f"CLIENT_NUMBER IN ({nums})")

    if timber_marks:
        marks = ",".join("'{}'".format(m.replace("'", "''"))
                         for m in sorted(set(timber_marks)))
        parts.append(f"TIMBER_MARK IN ({marks})")

    if districts:
        names = ",".join("'{}'".format(d.replace("'", "''"))
                         for d in sorted(set(districts)))
        parts.append(f"{district_field} IN ({names})")

    if rule:
        parts.extend(rule.sql())

    return " AND ".join(parts) if parts else "1=1"


def collection(feats: list[dict], where: str, extra: dict[str, Any] | None = None) -> dict:
    """Wrap features as a self-documenting FeatureCollection."""
    from datetime import datetime, timezone
    return {
        "type": "FeatureCollection",
        "name": "FTEN_CUT_BLOCK_POLY_SVW",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {
            "source": "WHSE_FOREST_TENURE.FTEN_CUT_BLOCK_POLY_SVW",
            "service": BLOCKS,
            "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "where": where,
            "feature_count": len(feats),
            "licence": ATTRIBUTION,
            **(extra or {}),
        },
        "features": feats,
    }


# ─────────────────────── identifier-level resolution ───────────────────────
#
# Everything above resolves a tenure HOLDER to their blocks. What follows
# resolves a single IDENTIFIER - one timber mark, one licence - to the blocks
# recorded against it. That is the finer of the two and the one that yields a
# plot-level claim.

LOOKUP_FIELDS = ",".join([
    "TIMBER_MARK", "CUT_BLOCK_ID", "CLIENT_NAME", "CLIENT_NUMBER",
    "CLIENT_LOCATION_CODE", "HARVEST_AUTH_FOREST_FILE_ID",
    "HARVEST_AUTH_CUTTING_PERMIT_ID", "CUT_BLOCK_FOREST_FILE_ID",
    "LIFE_CYCLE_STATUS_CODE", "HARVEST_AUTH_STATUS_CODE",
    "DISTURBANCE_START_DATE", "DISTURBANCE_END_DATE",
    "GEOGRAPHIC_DISTRICT_CODE", "GEOGRAPHIC_DISTRICT_NAME",
    "ADMIN_DISTRICT_CODE", "ADMIN_DISTRICT_NAME", "FEATURE_AREA",
])


def sql_quote(value: Any) -> str:
    return str(value).replace("'", "''")


def attributes(where: str, fields: str = LOOKUP_FIELDS,
               limit: int = PAGE, retries: int = 3) -> list[dict]:
    """Attributes only, no geometry.

    Cheap enough to use as a probe: decide whether a rung hit before paying
    for the polygons.

    RAISES on a service failure rather than returning an empty list. An empty
    list means "no such record", and a transient outage must not be allowed to
    mean the same thing - a blip on R1 silently demoted a cut block to a
    district envelope, which is a wrong answer that looks like a right one.
    """
    last = None
    for attempt in range(retries):
        try:
            data = _post(BLOCKS, {"where": where, "outFields": fields,
                                  "returnGeometry": "false",
                                  "resultRecordCount": limit, "f": "json"})
            return [f["attributes"] for f in data.get("features", [])]
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    raise ServiceError("query failed after {} attempts: {}".format(retries, last))


def attributes_safe(where: str, **kw) -> tuple[list[dict], str]:
    """attributes(), but a failure comes back as an error string.

    For callers that must not abort a whole run over one bad query - but that
    still need to tell a miss from an outage.
    """
    try:
        return attributes(where, **kw), ""
    except Exception as exc:
        return [], str(exc)


def by_field(field: str, value: str) -> tuple[list[dict], str, str]:
    """Exact match on one field.

    Returns (rows, where, error). The error is empty on success; a non-empty
    error means the service failed, which is not the same as no match.
    """
    where = "{} = '{}'".format(field, sql_quote(value).upper())
    rows, err = attributes_safe(where)
    return rows, where, err


def by_permit(file_id: str, permit: str) -> tuple[list[dict], str, str]:
    """File id AND cutting permit together.

    Never on the permit alone. Permit numbers are not unique across the
    province - matching '243' by itself returned 138 blocks under eleven
    unrelated licensees.
    """
    where = ("HARVEST_AUTH_FOREST_FILE_ID = '{}' AND "
             "HARVEST_AUTH_CUTTING_PERMIT_ID = '{}'".format(
                 sql_quote(file_id).upper(), sql_quote(permit).upper()))
    rows, err = attributes_safe(where)
    return rows, where, err


def client_locations(client_number: str) -> list[tuple[str, str]]:
    """Location codes FTEN holds for a client number, with the name it uses.

    Deduplicated here because returnDistinctValues is not honoured on this
    query - the raw response repeats the same pair up to a thousand times.
    """
    rows, _err = attributes_safe(
        "CLIENT_NUMBER = '{}'".format(sql_quote(client_number)),
        fields="CLIENT_LOCATION_CODE,CLIENT_NAME", limit=PAGE)
    seen, out = set(), []
    for a in rows:
        pair = ((a.get("CLIENT_LOCATION_CODE") or "").strip(),
                (a.get("CLIENT_NAME") or "").strip())
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def client_where(client_number: str, location: str = "",
                 district_code: str = "") -> str:
    where = "CLIENT_NUMBER = '{}'".format(sql_quote(client_number))
    if location:
        where += " AND CLIENT_LOCATION_CODE = '{}'".format(sql_quote(location))
    if district_code:
        where += " AND GEOGRAPHIC_DISTRICT_CODE = '{}'".format(
            sql_quote(district_code))
    return where


def in_window(row: dict, start_after: str | None = None,
              start_before: str | None = None,
              require_end: bool = False) -> bool:
    """Completion test applied to a returned row rather than in the WHERE.

    A date predicate in the query forces a full scan of 222,129 blocks and
    turns a sub-second identifier lookup into tens of seconds. An identifier
    returns a handful of rows, so filtering them here is instant and gives the
    same answer. CompletionRule.sql() remains correct for holder-level pulls,
    where the query is already broad.
    """
    from datetime import datetime, timezone

    def as_date(v):
        if v in (None, ""):
            return None
        try:
            return datetime.fromtimestamp(float(v) / 1000.0,
                                          tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            return None

    if require_end and as_date(row.get("DISTURBANCE_END_DATE")) is None:
        return False
    if not (start_after or start_before):
        return True
    d = as_date(row.get("DISTURBANCE_START_DATE"))
    if d is None:
        return False
    if start_after and str(d) < start_after:
        return False
    if start_before and str(d) > start_before:
        return False
    return True


def attributes_all(where: str, fields: str = LOOKUP_FIELDS,
                   log=None) -> list[dict]:
    """Every matching row, paged on OBJECTID.

    `attributes()` returns one page and stops. That is fine for an identifier
    lookup, which returns a handful of rows, but wrong for a holder query: a
    large tenure holder silently came back as exactly 1000 blocks, which was
    the page size rather than a count. Anything holder-scoped must use this.
    """
    need = fields if "OBJECTID" in fields else fields + ",OBJECTID"
    out, cursor, page_no = [], None, 0
    while True:
        w = where if cursor is None else "({}) AND OBJECTID > {}".format(
            where, cursor)
        try:
            data = _post(BLOCKS, {"where": w, "outFields": need,
                                  "returnGeometry": "false",
                                  "orderByFields": "OBJECTID",
                                  "resultRecordCount": PAGE, "f": "json"})
        except Exception as exc:
            if log:
                log("  ! {}".format(exc))
            break
        rows = [f["attributes"] for f in data.get("features", [])]
        if not rows:
            break
        out.extend(rows)
        page_no += 1
        if log:
            log("    page {}: {} rows (total {})".format(page_no, len(rows),
                                                         len(out)))
        ids = [r.get("OBJECTID") for r in rows if r.get("OBJECTID") is not None]
        if not ids:
            break
        nxt = max(ids)
        if cursor is not None and nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < PAGE:
            break
        time.sleep(0.2)
    return out


# ───────────────────────── catchment construction ──────────────────────────
#
# For a mark on private land there is no harvest geometry anywhere public. The
# best available answer is a bounded area: the district the mark was issued in,
# narrowed by what we know about it.
#
#   district           layer 748   ~10^6 ha    the outer bound
#   private ownership  layer 238   ~5% of BC   removes Crown land
#
# The intersect runs server-side - layer 238 is queried with the district
# polygon as a spatial filter - so a province of geometry is never loaded
# locally.
#
# Layer 238 is the Generalized Forest Cover Ownership layer, published by
# Forest Analysis and Inventory Branch alongside the VRI. Its field names are
# read at run time rather than hardcoded, because they have not been verified
# and a wrong guess here fails silently.

OWNERSHIP = ROOT + "/238/query"
OWNERSHIP_LAYER = ROOT + "/238"
DISTRICTS_LAYER = ROOT + "/748"

# Words that mark an ownership class as private. Matched against whatever
# descriptive field the layer turns out to carry.
PRIVATE_WORDS = ("PRIVATE", "CROWN GRANT", "FEE SIMPLE", "MUNICIPAL",
                 "INDIAN RESERVE", "FEDERAL RESERVE")


def _schema(url: str) -> list[dict]:
    try:
        return _post(url, {"f": "json"}).get("fields", [])
    except Exception:
        return []


def ownership_fields() -> dict[str, str]:
    """Which fields layer 238 actually carries.

    Returns {'code': ..., 'description': ...}; either may be absent. Discovered
    rather than assumed - see the module note above.
    """
    names = [f["name"] for f in _schema(OWNERSHIP_LAYER)
             if f.get("type") == "esriFieldTypeString"]
    out: dict[str, str] = {}
    for n in names:
        u = n.upper()
        if "description" not in out and "OWN" in u and "DESC" in u:
            out["description"] = n
        elif "code" not in out and "OWN" in u and ("CODE" in u or "SCHEDULE" in u):
            out["code"] = n
    if "description" not in out:
        for n in names:
            if "OWN" in n.upper():
                out["description"] = n
                break
    out["_all"] = ",".join(names[:20])
    return out


def ownership_values(field: str, limit: int = 200) -> list[str]:
    """Distinct values of an ownership field, so the private ones can be
    identified rather than guessed."""
    try:
        data = _post(OWNERSHIP, {"where": "1=1", "outFields": field,
                                 "returnDistinctValues": "true",
                                 "returnGeometry": "false",
                                 "resultRecordCount": limit, "f": "json"})
    except Exception:
        return []
    seen = []
    for f in data.get("features", []):
        v = (f["attributes"].get(field) or "").strip()
        if v and v not in seen:
            seen.append(v)
    return sorted(seen)


def district_geometry(district_code: str) -> dict | None:
    """The polygon for one NR district, by its code."""
    for field in ("DISTRICT_CODE", "ORG_UNIT", "ORG_UNIT_CODE"):
        try:
            data = _post(DISTRICTS_LAYER + "/query", {
                "where": "{} = '{}'".format(field, sql_quote(district_code)),
                "outFields": "DISTRICT_NAME", "returnGeometry": "true",
                "outSR": 4326, "resultRecordCount": 5, "f": "geojson"})
        except Exception:
            continue
        feats = data.get("features") or []
        if feats:
            return feats[0]
    return None


def private_catchment(district_code: str, log=None
                      ) -> tuple[list[dict], dict]:
    """Private forest land inside one district.

    Returns (features, diagnostics). The diagnostics record what was
    discovered about the layer and how much the intersect actually narrowed
    the area - because if it barely narrows it, the result is not worth
    calling a catchment.
    """
    diag: dict[str, Any] = {"district_code": district_code}

    district = district_geometry(district_code)
    if not district:
        diag["error"] = "district {} not found on layer 748".format(district_code)
        return [], diag

    fields = ownership_fields()
    desc = fields.get("description")
    diag["ownership_fields"] = fields.get("_all", "")
    if not desc:
        diag["error"] = "no ownership field found on layer 238"
        return [], diag
    diag["field_used"] = desc

    values = ownership_values(desc)
    private = [v for v in values
               if any(w in v.upper() for w in PRIVATE_WORDS)]
    diag["values_seen"] = len(values)
    diag["private_values"] = private
    if not private:
        diag["error"] = ("no value on {} matched a private ownership class - "
                         "values seen: {}".format(desc, ", ".join(values[:12])))
        return [], diag

    where = "{} IN ({})".format(
        desc, ",".join("'{}'".format(sql_quote(v)) for v in private))
    geom = district.get("geometry")
    try:
        data = _post(OWNERSHIP, {
            "where": where,
            "geometry": json.dumps(geom),
            "geometryType": "esriGeometryPolygon",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": 4326, "outSR": 4326,
            "outFields": desc, "returnGeometry": "true",
            "resultRecordCount": PAGE, "f": "geojson"})
    except Exception as exc:
        diag["error"] = "ownership query failed: {}".format(exc)
        return [], diag

    feats = data.get("features") or []
    diag["features"] = len(feats)
    if len(feats) >= PAGE:
        diag["truncated"] = True
        diag["note"] = ("hit the page limit - the catchment is incomplete and "
                        "the area below is a floor, not a total")
    if log:
        log("    catchment {}: {} private polygons".format(district_code,
                                                           len(feats)))
    return feats, diag
