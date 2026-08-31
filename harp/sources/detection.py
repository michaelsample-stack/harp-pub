"""Satellite harvest detection, via tracemark-eo.

For suppliers who can give us a coordinate or a catchment but no polygon.
Geometry is derived rather than retrieved, so it is the weakest of the five
paths - but it is the only option where no record exists.

TWO METHODS, AND THE DIFFERENCE MATTERS
---------------------------------------
tracemark-eo has two functions that both take polygons. They are not
interchangeable.

    pointtopoly.polyToChangeDetectionPoly_DIST()
        Dissolves every detection inside each search polygon into ONE feature:
            ee.Feature(changePoly.geometry(), {"alert": "True"})
        Correct for a 1 km buffer around a point - that is one harvest.
        WRONG for a catchment: you get a single multipart blob containing every
        harvest in the region, which is useless for per-plot reporting.

    harvest_generation.generate_change_detection_polys()
        Takes a `sourcing_asset`, covers it with a 200 km grid, and vectorises
        per tile - producing SEPARATE polygons. This is the catchment method.

So: points use the first, catchments use the second. Do not reuse the wrong one.

STATUS: stub. Blocked on confirming how we call tracemark-eo - as a library, or
via the deployed API Evan pointed at. That is an architecture decision, not a
coding one.
"""

from __future__ import annotations

from typing import Any


def from_points(points: list[dict], start: str, end: str, buffer_m: int = 1000) -> list[dict]:
    """Detect a harvest around each supplied coordinate.

    Wraps the point-to-polygon path. Each point becomes one search buffer and
    yields at most one harvest polygon, or a flagged centroid if nothing is
    found.
    """
    raise NotImplementedError(
        "Pending the decision on how HARP calls tracemark-eo."
    )


def from_catchment(catchment: dict, start: str, end: str) -> list[dict]:
    """Detect every harvest inside a supplier's declared sourcing area.

    Wraps the regional sweep. Returns one feature per detected harvest, not one
    per catchment.

    Note this is deliberately over-inclusive: a catchment is a search box, not
    a claim. Over-declaring is permitted under EUDR; under-declaring is not.
    """
    raise NotImplementedError(
        "Pending the decision on how HARP calls tracemark-eo."
    )
