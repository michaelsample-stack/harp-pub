"""The harvest detection service - submit, wait, and read what comes back.

Two endpoints, and neither behaves quite as its name suggests.

    POST /upload/process        multipart: file, startDate, endDate
    GET  /upload/status/{id}    poll until completed, then a signed URL

WHAT IT ACTUALLY RETURNS
------------------------
A file named `.geojson` that is usually a CSV:

    geo             the geometry as WKT
    date            when the disturbance was first detected
    area_ha         its size
    feature_type    'polygon', or 'point' below four hectares
    geo_json_str    the same geometry, already as GeoJSON
    sce_id          a batch id for the whole job, not a per-feature id

Two of those are worth knowing about in advance. `geo_json_str` means no WKT
parsing is needed - the rows already carry proper GeoJSON. And `sce_id` is one
value across every row, so it is not something to join on, however much it
looks like an identifier.

WHY THE FILENAME IS NOT TRUSTED
-------------------------------
The service names everything `.geojson`. A CSV arriving under that name looked
like a corrupt file for twenty minutes before anyone read the first line. The
content is checked instead: a leading `{` or `[` means JSON, anything else is
a table.

WHAT A SUB-FOUR-HECTARE DETECTION LOOKS LIKE
--------------------------------------------
A point, not a polygon - but it still carries an area. Measured against a
Georgia control: points ran 1.00 to 3.99 ha, polygons 4.01 to 515. So a small
detection is a centroid with a known size rather than a boundary, and it is
still worth keeping.

ONE POLYGON GOES IN
-------------------
The service takes crude, large bounding areas. Handing it a constellation of
small polygons is not what it is for, so the search areas are unioned before
submission and attribution is recovered afterwards by spatial join. The union
is a submission artefact and is never declared.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime

DEFAULT_BASE = "https://harmac-api-test-183302365043.us-west1.run.app"

# Observed: a job over a million hectares completed in six seconds. Two
# seconds between polls is unobtrusive and the ceiling is generous.
POLL_SECONDS = 2
MAX_WAIT_SECONDS = 900


class DetectionError(RuntimeError):
    """The service refused, failed, or returned something unusable."""


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "NGIS-HARP/1.0"})
    return s


def submit(path: str, start: str, end: str, base: str = DEFAULT_BASE,
           token: str = "", log=print) -> str:
    """Send a file for detection. Returns the job id."""
    s = _session()
    if token:
        s.headers["Authorization"] = "Bearer " + token
    size = os.path.getsize(path) / (1024 * 1024)
    log("submitting {} ({:.2f} MB), {} to {}".format(
        os.path.basename(path), size, start, end))
    with open(path, "rb") as fh:
        r = s.post(base.rstrip("/") + "/upload/process",
                   files={"file": (os.path.basename(path), fh,
                                   "application/geo+json")},
                   data={"startDate": start, "endDate": end},
                   timeout=600)
    if not r.ok:
        raise DetectionError("submit failed, HTTP {}: {}".format(
            r.status_code, r.text[:400]))
    try:
        body = r.json()
    except ValueError:
        raise DetectionError("submit returned non-JSON: " + r.text[:300])
    job = body.get("jobId")
    if not job:
        raise DetectionError("no jobId in the response: " + json.dumps(body))
    log("  job {}".format(job))
    return job


def wait(job: str, base: str = DEFAULT_BASE, token: str = "",
         log=print) -> dict:
    """Poll until the job finishes. Returns the status body."""
    s = _session()
    if token:
        s.headers["Authorization"] = "Bearer " + token
    t0 = time.time()
    last = ""
    while time.time() - t0 < MAX_WAIT_SECONDS:
        r = s.get("{}/upload/status/{}".format(base.rstrip("/"), job),
                  timeout=120)
        if not r.ok:
            raise DetectionError("status failed, HTTP {}: {}".format(
                r.status_code, r.text[:300]))
        try:
            body = r.json()
        except ValueError:
            raise DetectionError("status returned non-JSON: " + r.text[:300])
        state = body.get("status", "?")
        if state != last:
            # Elapsed time is diagnostic. A job that finishes almost instantly
            # has usually found nothing; one that takes several seconds has
            # usually found something.
            log("  {:>6.1f}s  {}".format(time.time() - t0, state))
            last = state
        if state == "completed":
            return body
        if state == "failed":
            msg = body.get("errorMessage") or "(no message given)"
            raise DetectionError("the job failed: {}{}".format(
                msg, _explain(msg)))
        time.sleep(POLL_SECONDS)
    raise DetectionError("gave up after {} minutes".format(
        MAX_WAIT_SECONDS // 60))


def _explain(msg: str) -> str:
    """Say what a known failure actually means. Cheap, and saves an hour."""
    m = (msg or "").lower()
    if "db-dtypes" in m:
        return ("\n  That is a missing Python package on the service, not a "
                "problem with the file. google-cloud-bigquery needs db-dtypes "
                "to read date columns - and its absence also strips the date "
                "field from an otherwise successful result.")
    if "timeout" in m or "deadline" in m:
        return "\n  The query ran too long. A narrower window would confirm it."
    if "memory" in m or "resource" in m:
        return "\n  The service ran out of room. Try a smaller area."
    return ""


def download(status: dict, out_dir: str, job: str, log=print) -> str:
    """Fetch the result and save it under an extension matching its contents.

    The service names everything `.geojson` regardless of what it sends, so
    the first bytes decide. Saving a CSV as `.geojson` is how a perfectly good
    result came to look like a corrupt one.
    """
    import requests
    urls = status.get("downloadUrls") or []
    if not urls:
        raise DetectionError("completed, but with no download URL")
    r = requests.get(urls[0], timeout=900)
    if not r.ok:
        raise DetectionError("download failed, HTTP {}".format(r.status_code))
    raw = r.content
    log("  {:,} bytes".format(len(raw)))

    head = raw[:400].decode("utf-8", "replace").lstrip()
    ext = "geojson" if head.startswith(("{", "[")) else "csv"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, "detections-raw-{}-{}.{}".format(
        stamp, job[:8], ext))
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


# ─────────────────────────── reading the result ────────────────────────────

def to_features(path: str, log=print) -> list[dict]:
    """Whatever came back, as GeoJSON features.

    A CSV row already carries proper GeoJSON in `geo_json_str`, so the WKT in
    `geo` is only a fallback for a return that lacks it.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    head = raw[:400].decode("utf-8", "replace").lstrip()

    if head.startswith(("{", "[")):
        gj = json.loads(raw.decode("utf-8"))
        feats = gj.get("features") or []
        log("  GeoJSON, {:,} feature(s)".format(len(feats)))
        return feats

    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    if not rows:
        cols = head.splitlines()[0] if head.strip() else ""
        log("  CSV with a header and no rows: {}".format(cols))
        return []

    log("  CSV, {:,} row(s)".format(len(rows)))
    feats, no_geom = [], 0
    for row in rows:
        geom = None
        blob = (row.get("geo_json_str") or "").strip()
        if blob:
            try:
                geom = json.loads(blob)
            except ValueError:
                geom = None
        if geom is None:
            wkt = (row.get("geo") or "").strip()
            if wkt:
                try:
                    from shapely import wkt as shapely_wkt
                    from shapely.geometry import mapping
                    geom = mapping(shapely_wkt.loads(wkt))
                except Exception:
                    geom = None
        if geom is None:
            no_geom += 1
            continue
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "date": row.get("date", ""),
            "area_ha": row.get("area_ha", ""),
            "feature_type": row.get("feature_type", ""),
            # Kept, but it is a batch id for the whole job rather than a
            # per-feature one - not something to join on.
            "sce_batch_id": row.get("sce_id", ""),
        }})
    if no_geom:
        log("  {} row(s) had no readable geometry".format(no_geom))
    return feats


def describe(feats: list[dict], log=print) -> dict:
    """What came back, in the terms that matter."""
    from collections import Counter
    kinds = Counter((f.get("properties") or {}).get("feature_type")
                    or "unspecified" for f in feats)
    dates = sorted((f.get("properties") or {}).get("date", "")[:10]
                   for f in feats if (f.get("properties") or {}).get("date"))
    area = 0.0
    for f in feats:
        try:
            area += float((f.get("properties") or {}).get("area_ha") or 0)
        except (TypeError, ValueError):
            pass

    log("  " + ", ".join("{:,} {}".format(n, k) for k, n in kinds.most_common()))
    if area:
        log("  {:,.0f} ha detected".format(area))
    if dates:
        log("  dated {} to {}".format(dates[0], dates[-1]))
    undated = len(feats) - len(dates)
    if undated:
        log("  {:,} carry no date and cannot be placed in a window".format(
            undated))
    return {"features": len(feats), "kinds": dict(kinds), "area_ha": area,
            "first": dates[0] if dates else "", "last": dates[-1] if dates
            else "", "undated": undated}


def run(union_path: str, start: str, end: str, out_dir: str,
        base: str = DEFAULT_BASE, token: str = "", log=print) -> tuple:
    """Submit, wait, download, read. Returns (features, raw_path, summary)."""
    job = submit(union_path, start, end, base, token, log)
    status = wait(job, base, token, log)
    raw = download(status, out_dir, job, log)
    feats = to_features(raw, log)

    if not feats:
        # An empty result is a real answer, but it is not a month's work and
        # should not be written as one. Today it means the detection table
        # does not yet cover this region; another day it could mean a broken
        # submission, and those must not look alike.
        raise DetectionError(
            "the service returned nothing for this area and window.\n"
            "  That is a coverage or window answer rather than an error - but "
            "it is not a month, so nothing has been written.\n"
            "  The raw response is at {}".format(raw))

    summary = describe(feats, log)
    summary["job"] = job
    summary["raw"] = raw
    return feats, raw, summary
