"""Geodata supplied directly by the supplier.

The best of the five acquisition paths - a 1:1 polygon, no derivation. Private
land in particular has no public source in BC, so direct supply is the only
route.

Work here is validation and repair, not resolution:

    read      accept GeoJSON, shapefile, KML, GeoPackage
    reproject to WGS84
    repair    the geometry cleaning chain (spikes, holes, slivers, winding)
    validate  eudr_geojson
    attribute tie to a supplier from the register

STATUS: stub. Waiting on a real supplier file to design against - guessing at
the shape of other people's data is how you build the wrong thing.
"""

from __future__ import annotations

from typing import Any


def read(path: str) -> list[dict]:
    """Read a supplier file into GeoJSON features in WGS84."""
    raise NotImplementedError(
        "supplier_file.read is not implemented yet. "
        "Waiting on a sample from Harmac."
    )


def validate(features: list[dict], profile: str = "supplier_submission") -> list[dict]:
    """Run eudr_geojson and return findings.

    Two profiles matter here:

        supplier_submission   expects ProducerName, ProducerCountry, Area
        machine_geometry      derived polygons that will never have those

    Billerud currently handles this by running everything and filtering nine
    findings out by display label afterwards - brittle, and invisible to anyone
    reading the library. A profile parameter is the fix.
    """
    raise NotImplementedError("Pending the profile parameter in eudr_geojson.")
