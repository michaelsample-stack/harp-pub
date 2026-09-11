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
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

ROOT = "https://delivery.maps.gov.bc.ca/arcgis/rest/services/mpcm/bcgwpub/MapServer"
BLOCKS = ROOT + "/340/query"
DISTRICTS = ROOT + "/748/query"

PAGE = 1000
TIMEOUT = 180

# ── the same register, a second door ────────────────────────────────────────
#
# The ArcGIS REST service went down for a day and took every run with it. The
# same feature class is published as WFS on a different host, and it answered
# throughout: identical fields, identical records - GR2106 gives A94731,
# BLK227 and CAPE MUDGE FORESTRY LTD. either way.
#
# So a run no longer depends on one server being up. REST is tried first
# because it pages better and is what the rest of this module assumes; WFS is
# tried when REST fails outright.
WFS = "https://openmaps.gov.bc.ca/geo/pub/wfs"
WFS_LAYER = "pub:WHSE_FOREST_TENURE.FTEN_CUT_BLOCK_POLY_SVW"

# A request that fails is tried again before being believed.
#
# The service rate-limits, and a limited request answers perfectly well thirty
# seconds later. Trying once and giving up turned a throttle into a month where
# 190 of 221 sources resolved to nothing - and because each failure was handled
# correctly per source, the run finished looking healthy.
RETRIES = 3
BACKOFF = (2, 8, 20)

# Identifiers per IN clause.
#
# Started at 200 and the service refused every batch - "Error performing query
# operation", which is what it says for a where clause it cannot parse as much
# as for one that is too long. A batch is only as good as its worst member,
# and this data contains identifiers that are not marks at all: mill towns,
# the slash form, and at least one mark ending in an apostrophe.
#
# So the batch is small to begin with and halves on failure until it isolates
# whatever the service objected to. One bad identifier then costs one query
# instead of two hundred.
BATCH = 50


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


def _post_once(url: str, params: dict) -> dict:
    """One attempt, no retry.

    For a query whose failure is the query's own fault. Once the service has
    been shown to answer, a where clause it rejects will be rejected again -
    retrying it three times with backoff costs ninety seconds to learn
    nothing, and doing that while splitting a batch is how a prefetch came to
    take hours.
    """
    r = requests.post(url, data=params, timeout=TIMEOUT)
    if r.status_code == 429 or r.status_code >= 500:
        raise ServiceError("HTTP {}".format(r.status_code))
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise ServiceError(data["error"].get("message", str(data["error"])))
    return data


def _retryable(exc, status: int = 0) -> bool:
    """Is this worth trying again?

    A timeout, a rate limit, or a server-side error will often answer on the
    next attempt. A 400 will not - the request is malformed and will be
    malformed again, so retrying it three times with backoff costs ninety
    seconds to learn what the first attempt already said.

    The service's own `Error performing query operation` is retryable. It is
    what ArcGIS says when its backend errors, which happens transiently and
    also happens for a day at a time - the probe is what tells those apart,
    not this.
    """
    if status == 429 or status >= 500:
        return True
    if 400 <= status < 500:
        return False
    text = str(exc).lower()
    return any(t in text for t in ("timeout", "timed out", "connection",
                                   "error performing query"))


def _post(url: str, params: dict, log=None) -> dict:
    """One request, retried where retrying could help.

    Retrying a malformed request is how a five-minute problem becomes an
    afternoon: three attempts with backoff, on every call, for an answer that
    was never going to change.
    """
    last = None
    for attempt in range(RETRIES):
        status = 0
        try:
            r = requests.post(url, data=params, timeout=TIMEOUT)
            status = r.status_code
            if status == 429 or status >= 500:
                raise ServiceError("HTTP {}".format(status))
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                # An error in the body carries its own code; the HTTP status
                # is 200 either way.
                status = int((data["error"] or {}).get("code") or 0)
                raise ServiceError(data["error"].get("message",
                                                     str(data["error"])))
            return data
        except Exception as exc:
            last = exc
            if attempt == RETRIES - 1 or not _retryable(exc, status):
                break
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            if log:
                log("    service said no ({}), waiting {}s and trying "
                    "again".format(str(exc).splitlines()[0][:70], wait))
            time.sleep(wait)
    raise ServiceError(str(last).splitlines()[0] if last else "unknown")


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
        try:
            data = _post(BLOCKS, {
                "where": w,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": 4326,
                "orderByFields": "OBJECTID",
                "resultRecordCount": PAGE,
                "f": "geojson",
            })
        except Exception as exc:
            if page_no:
                # Partway through paging. Half a set of blocks is worse than
                # none, so this is a failure rather than a short answer.
                raise
            log("    REST is not answering ({}), trying WFS".format(
                str(exc).splitlines()[0][:70]))
            return wfs_query(where, geometry=True, log=log)
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


def _cql(where: str) -> str:
    """An ArcGIS where clause as CQL.

    The two are close enough for what this module builds - equality, IN, and
    AND - so nothing is translated. What differs is that CQL has no square
    brackets and no schema prefix, neither of which appear here.
    """
    return where


def wfs_query(where: str, geometry: bool = False, log=None) -> list[dict]:
    """The same query against the WFS endpoint.

    Returns GeoJSON features. Geometry comes back in BC Albers unless asked
    for otherwise, so the CRS is named explicitly - a run that quietly got
    metres where it expected degrees would place every block off the coast of
    Africa.
    """
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": WFS_LAYER, "outputFormat": "application/json",
        "srsName": "EPSG:4326", "count": PAGE,
        "CQL_FILTER": _cql(where),
    }
    out, start = [], 0
    while True:
        params["startIndex"] = start
        r = requests.get(WFS, params=params, timeout=TIMEOUT)
        if not r.ok:
            raise ServiceError("WFS returned HTTP {}".format(r.status_code))
        try:
            body = r.json()
        except ValueError:
            msg = " ".join(re.sub(r"<[^>]+>", " ", r.text).split())[:200]
            raise ServiceError("WFS did not return JSON: {}".format(msg))
        feats = body.get("features") or []
        out.extend(feats)
        if len(feats) < PAGE:
            break
        start += PAGE
        if start > 20000:
            break
    if log:
        log("    {} feature(s) from WFS".format(len(out)))
    return out


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
    # REST is unreachable. The same records are on WFS, so ask there before
    # concluding the register has nothing - an outage that reads as "no such
    # record" demotes a cut block to a district envelope, which is a wrong
    # answer that looks like a right one.
    try:
        feats = wfs_query(where, geometry=False)
        return [f.get("properties") or {} for f in feats]
    except Exception as wfs_exc:
        raise ServiceError(
            "REST failed after {} attempts ({}), and WFS also failed "
            "({})".format(retries, str(last).splitlines()[0][:80],
                          str(wfs_exc).splitlines()[0][:80]))


def attributes_safe(where: str, **kw) -> tuple[list[dict], str]:
    """attributes(), but a failure comes back as an error string.

    For callers that must not abort a whole run over one bad query - but that
    still need to tell a miss from an outage.
    """
    try:
        return attributes(where, **kw), ""
    except Exception as exc:
        return [], str(exc)


# ─────────────────────────────── prefetch ─────────────────────────────────
#
# The ladder asks one question per identifier per rung: 221 identifiers across
# three fields is 660 requests, one after another, nothing cached between runs.
# That is what gets a run throttled, and re-running the same month asks every
# one again from scratch.
#
# A prefetch asks the same questions in batches - `TIMBER_MARK IN (...)` with
# two hundred marks at a time - and hands the ladder an index to read instead.
# Three fields at fifteen requests is forty times fewer, comfortably below any
# throttle, and a cached index makes a re-run nearly free.
#
# It is an index, not a replacement. A rung that finds nothing in it still
# falls through to a live query, because the prefetch only knows about the
# identifiers it was given.

PREFETCH_FIELDS = ("TIMBER_MARK", "HARVEST_AUTH_FOREST_FILE_ID",
                   "CUT_BLOCK_FOREST_FILE_ID")


class Index:
    """What a batch of identifiers matched, by field and value.

    `known` records which fields were successfully fetched. A field that
    failed is not in it, so the ladder falls through to a live query for that
    field rather than treating an outage as a miss - which is the distinction
    that matters most here.
    """

    def __init__(self):
        self.rows: dict[tuple, list] = {}
        self.known: set = set()
        # Identifiers the service refused even on their own. These were never
        # answered, so they are not misses, and the ladder must still ask.
        self.refused: set = set()

    def get(self, field: str, value: str):
        """Rows for one identifier, or None if it was not asked about.

        None means fall through to a live query. That happens for a field
        whose prefetch failed entirely, and for a single identifier the
        service refused - both are questions nobody answered, and reading
        either as a miss would say the register does not have something it
        was never asked for.
        """
        if field not in self.known:
            return None
        key = (field, str(value or "").strip().upper())
        if key in self.refused:
            return None
        return self.rows.get(key, [])

    def __len__(self):
        return sum(len(v) for v in self.rows.values())


def _fetch_batched(field: str, values: list, log=print, size: int = BATCH):
    """Every value for one field, in batches.

    Returns (rows, refused). `rows` is None if the field could not be fetched
    at all, which means the ladder should query as it goes.

    **Probe, batch, fall back once.** A single identifier is tried first: if
    that fails the service is not answering and the field is abandoned in one
    request rather than discovering the same thing fifty times. A batch that
    fails afterwards is retried as individual queries - once, not recursively.

    An earlier version halved a failed batch and halved again down to single
    identifiers. That was built to isolate one malformed identifier poisoning
    a batch of fifty, a failure mode that was guessed at and never observed:
    when the whole thing failed it was because BCGW's backend was down, and a
    bare `where=1=1` count failed the same way from a browser. Recursion plus
    the retry and its backoff turned a five-minute outage into hours of
    silent waiting.
    """
    out, refused = [], set()
    if not values:
        return out, refused

    # One identifier, with the normal retry, to see whether the field answers.
    probe, err = attributes_safe("{} IN ('{}')".format(
        field, sql_quote(values[0])))
    if err:
        log("    {} is not answering: {}".format(
            field, str(err).splitlines()[0][:90]))
        return None, refused
    out.extend(probe)

    done, remaining = 1, values[1:]
    for start in range(0, len(remaining), size):
        chunk = remaining[start:start + size]
        quoted = ",".join("'{}'".format(sql_quote(v)) for v in chunk)
        rows, error = _lookup_once("{} IN ({})".format(field, quoted))
        if not error:
            out.extend(rows)
            done += len(chunk)
        else:
            # One fallback level. The batch failed, so ask about each of its
            # identifiers on its own - which is what the ladder would have
            # done anyway, so nothing is lost and the bad one is named.
            log("    a batch of {} failed, asking individually".format(
                len(chunk)))
            for v in chunk:
                rows, error = _lookup_once("{} = '{}'".format(
                    field, sql_quote(v)))
                if error:
                    refused.add(v)
                else:
                    out.extend(rows)
                done += 1
        log("    {:<30}{:>4}/{:<5} identifier(s)".format(
            field, done, len(values)))
    return out, refused


def prefetch(identifiers, fields=PREFETCH_FIELDS, log=print) -> Index:
    """Ask about every identifier at once, a field at a time."""
    idx = Index()
    values = sorted({str(v or "").strip().upper() for v in identifiers
                     if str(v or "").strip()})
    if not values:
        return idx

    log("prefetching {:,} identifier(s) across {} field(s)".format(
        len(values), len(fields)))
    for field in fields:
        rows, bad = _fetch_batched(field, values, log=log)
        if rows is None:
            # Every attempt failed, right down to single identifiers. That is
            # the service being unavailable rather than one bad value, and an
            # incomplete index is worse than none - a miss in it would read as
            # "not in the register" when it means "not asked".
            log("  {} could not be prefetched".format(field))
            continue
        for r in rows:
            key = (field, str(r.get(field) or "").strip().upper())
            idx.rows.setdefault(key, []).append(r)
        # Everything asked about and answered, including the misses - so a
        # re-run needs none of it.
        for v in values:
            if v in bad:
                continue
            remember(field, v, idx.rows.get((field, v), []))
        for v in bad:
            idx.refused.add((field, str(v or "").strip().upper()))
        idx.known.add(field)
        hits = len({k[1] for k in idx.rows if k[0] == field})
        note = ""
        if bad:
            # Named, because an identifier the register cannot be asked about
            # is a finding - it is usually malformed rather than absent.
            note = "   ({} identifier(s) the service refused: {})".format(
                len(bad), ", ".join(sorted(bad)[:4]))
        log("  {:<32}{:>6,} row(s) against {:,} identifier(s){}".format(
            field, len(rows), hits, note))

    if not idx.known:
        log("  nothing prefetched - the ladder will query as it goes")
    return idx


# Set by a run so lookups survive between them. A module-level handle rather
# than a parameter threaded through six call sites, because every one of them
# would pass the same thing.
_cache = None


def use_cache(cache) -> None:
    """Give the source a cache to read and write. None disables it."""
    global _cache
    _cache = cache


def cached_by_field(field: str, value: str) -> tuple[list[dict], str] | None:
    """What the cache knows about one identifier, or None if it knows nothing.

    A miss is cached too, and separately: "the register does not have this"
    is an answer worth keeping, and it expires sooner because a mark absent
    today may be issued next month.
    """
    if _cache is None:
        return None
    key = "{}={}".format(field, str(value or "").strip().upper())
    hit = _cache.get("ften_lookup", key)
    if hit is not None:
        return hit, ""
    if _cache.get("ften_miss", key) is not None:
        return [], ""
    return None


def remember(field: str, value: str, rows: list) -> None:
    if _cache is None:
        return
    key = "{}={}".format(field, str(value or "").strip().upper())
    if rows:
        _cache.put("ften_lookup", key, rows)
    else:
        _cache.put("ften_miss", key, True)


def _lookup_once(where: str) -> tuple[list[dict], str]:
    """Attributes for a where clause, one attempt, error as a string.

    Falls through to WFS the same way `attributes()` does. Without that, a
    REST outage made every prefetch batch fail and the whole thing degrade to
    one query per identifier - which is the cost the prefetch exists to
    avoid, paid in full while the fallback sat unused one function away.
    """
    try:
        data = _post_once(BLOCKS, {
            "where": where, "outFields": LOOKUP_FIELDS,
            "returnGeometry": "false", "resultRecordCount": PAGE,
            "f": "json"})
        return [f.get("attributes", {}) for f in data.get("features", [])], ""
    except Exception as exc:
        try:
            feats = wfs_query(where, geometry=False)
            return [f.get("properties") or {} for f in feats], ""
        except Exception:
            # Both doors shut. The REST error is the more useful of the two
            # to report - WFS is the fallback, not the thing that broke.
            return [], str(exc).splitlines()[0][:160]


def by_field(field: str, value: str, index=None) -> tuple[list[dict], str, str]:
    """Exact match on one field.

    Returns (rows, where, error). The error is empty on success; a non-empty
    error means the service failed, which is not the same as no match.
    """
    where = "{} = '{}'".format(field, sql_quote(value).upper())
    if index is not None:
        hit = index.get(field, value)
        if hit is not None:
            # The index was built for this field, so an empty list is a real
            # miss rather than a question nobody asked.
            return hit, where, ""
    known = cached_by_field(field, value)
    if known is not None:
        return known[0], where, known[1]
    rows, err = attributes_safe(where)
    if not err:
        # Only a real answer is remembered. A service error is not a fact
        # about the identifier, and caching one would make an outage
        # permanent.
        remember(field, value, rows)
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
    failures = 0
    for field in ("DISTRICT_CODE", "ORG_UNIT", "ORG_UNIT_CODE"):
        try:
            data = _post(DISTRICTS_LAYER + "/query", {
                "where": "{} = '{}'".format(field, sql_quote(district_code)),
                "outFields": "DISTRICT_NAME", "returnGeometry": "true",
                "outSR": 4326, "resultRecordCount": 5, "f": "geojson"})
        except Exception:
            # Which field carries the code varies, so a failure here is
            # usually the wrong field rather than a broken service - unless
            # every one of them fails, which is checked below.
            failures += 1
            continue
        feats = data.get("features") or []
        if feats:
            return feats[0]
    if failures == 3:
        # Every field failed, so nothing was actually asked. Returning None
        # here would say the district does not exist, and a search area built
        # on that answer is a district-shaped hole rather than a district.
        raise ServiceError(
            "the district layer did not answer for {} on any field - this is "
            "an outage, not a missing district".format(district_code))
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
