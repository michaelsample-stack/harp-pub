"""Adapters for the NGIS libraries HARP depends on.

Three libraries sit outside this repo and are imported lazily, so HARP runs
without them and tells you exactly what is missing when it needs one:

    eudr_clean      geometry repair - spikes, slivers, holes, ring winding
    eudr_geojson    EUDR schema validation against a named profile
    ngis-eo         satellite harvest detection (tracemark-eo)

WHY LAZY, AND WHY HERE
----------------------
Resolving a BC timber mark needs none of them. Requiring all three to import
would mean a BC-only run fails on a missing Earth Engine credential, which is
absurd. Equally, scattering try/except ImportError through the pipeline makes
it impossible to see what HARP actually depends on. One module, one place.

Each adapter fails with a message naming the library and what it was wanted
for, rather than a bare ImportError forty frames deep.

STATUS
------
eudr_clean and eudr_geojson are confirmed against the real packages
(eudr_geojson 0.4.0, eudr_clean 0.5.5). ngis-eo is still provisional.
"""

from __future__ import annotations

from typing import Any


class MissingLibrary(RuntimeError):
    """A required NGIS library is not installed."""

    def __init__(self, name: str, wanted_for: str, install: str = ""):
        msg = "{} is needed to {}.".format(name, wanted_for)
        if install:
            msg += "  Install: {}".format(install)
        super().__init__(msg)
        self.library = name


# ──────────────────────────── eudr_clean ───────────────────────────────────

def clean(collection: dict, **options: Any) -> dict:
    """Repair geometry. eudr_clean.clean_file, confirmed against 0.5.5.

    Takes a FeatureCollection dict and returns:

        {"valid_features": [...], "failed_features": [...],
         "stats": {...}, "warnings": [...], "log": "...", "failures": [...]}

    TWO THINGS TO KNOW
    ------------------
    The feature count changes. MultiPolygon explosion and bow-tie splitting
    both turn one input feature into several, so positional indices are
    meaningless afterwards. Track features by a property, never by index.

    Everything destructive is opt-in and left off by default here. Hole
    filling, vertex collapsing, small-polygon-to-point conversion and property
    purging all change what is being asserted about a plot, so they are
    decisions for a caller who knows the client's position - not defaults.

    `verbose` is forced off: the library logs to stdout, which would bury a
    pipeline run.
    """
    try:
        from eudr_clean import clean_file       # type: ignore
    except ImportError as exc:
        raise MissingLibrary(
            "eudr_clean", "repair geometry",
            "pip install eudr-clean") from exc
    options.setdefault("verbose", False)
    return clean_file(collection, **options)


# ─────────────────────────── eudr_geojson ──────────────────────────────────

def validate(collection: dict, country_iso2: str | None = None,
             **options: Any) -> list[dict]:
    """Validate against the TraceMark QA taxonomy. eudr_geojson.validate_file,
    confirmed against 0.4.0.

    Returns a flat list of finding dicts. An empty list means clean. Each
    finding carries:

        feature_id, sub_index, production_place, error_code, error_type,
        label, notes, geometry_type, wkt

    `error_type` is 'Required' or 'Recommended'. Filter on it rather than on
    the label - the Billerud production instance filters findings by display
    label, which silently drops any finding whose label does not match, and
    that is exactly the bug to avoid repeating.

    Never raises: the library returns internal errors as 1.1.1 findings.
    """
    try:
        from eudr_geojson import validate_file  # type: ignore
    except ImportError as exc:
        raise MissingLibrary(
            "eudr_geojson", "validate against the EUDR taxonomy",
            "pip install eudr-geojson") from exc
    return validate_file(collection, country_iso2=country_iso2, **options)


def required_only(findings: list[dict]) -> list[dict]:
    """The findings that actually block. Recommended ones are reported and
    carried, not cleaned for."""
    return [f for f in findings
            if str(f.get("error_type", "")).lower() == "required"]


# ───────────────────────────── ngis-eo ─────────────────────────────────────

def detect_point(lat: float, lon: float, start: str, end: str,
                 buffer_m: int = 1000, **options: Any) -> list[dict]:
    """Harvest detection around a coordinate.

    PROVISIONAL SIGNATURE. Confirm against the package.

    Uses pointtopoly.polyToChangeDetectionPoly_DIST, which dissolves every
    detection inside the search polygon into ONE feature. That is correct here
    - a 1 km buffer around a delivery point is one harvest.
    """
    try:
        import ngis_eo                          # type: ignore
    except ImportError as exc:
        raise MissingLibrary(
            "ngis-eo", "detect harvest around a coordinate",
            "pip install ngis-eo  (needs Earth Engine credentials)") from exc
    return ngis_eo.detect_point(lat=lat, lon=lon, start=start, end=end,
                                buffer_m=buffer_m, **options)


def detect_catchment(boundary: dict, start: str, end: str,
                     **options: Any) -> list[dict]:
    """Harvest detection across a catchment boundary.

    PROVISIONAL SIGNATURE. Confirm against the package.

    Must use harvest_generation.generate_change_detection_polys, which grids
    the area and vectorises per tile, producing SEPARATE polygons. Do NOT use
    the point method here: it returns a single multipart blob containing every
    harvest in the region, which cannot be reported per plot.
    """
    try:
        import ngis_eo                          # type: ignore
    except ImportError as exc:
        raise MissingLibrary(
            "ngis-eo", "detect harvest across a catchment",
            "pip install ngis-eo  (needs Earth Engine credentials)") from exc
    return ngis_eo.detect_catchment(boundary=boundary, start=start, end=end,
                                    **options)


def available() -> dict[str, bool]:
    """Which libraries are importable. Report this at the head of a run so a
    failure four minutes in is not a surprise."""
    out = {}
    for name, module in (("eudr_clean", "eudr_clean"),
                         ("eudr_geojson", "eudr_geojson"),
                         ("ngis-eo", "ngis_eo")):
        try:
            __import__(module)
            out[name] = True
        except ImportError:
            out[name] = False
    return out
