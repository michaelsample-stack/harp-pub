"""Acquisition adapters.

Each module resolves harvest geometry from one kind of evidence:

    ften            BC forest tenure, by timber mark or client number
    supplier_file   geodata supplied directly by the supplier
    detection       satellite detection, via tracemark-eo

They share no base class deliberately - the inputs are too different. What they
share is the output contract: a list of GeoJSON features plus provenance.
"""

from . import dmp  # noqa: F401
