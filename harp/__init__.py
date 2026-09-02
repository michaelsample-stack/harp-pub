"""HARP - Harvest Area Resolution Pipeline.

Resolves harvest area geometry from whatever evidence a supplier can provide -
a timber mark, a coordinate, a catchment boundary, or a shapefile - and lands
it in TraceMark as sce_base rows.

Client-agnostic. A client is a YAML file, not a code fork.

    from harp import identify, router, assemble, validate, normalise

    records = identify.load("SOURCE.xlsx")
    results = [router.resolve(identify.identify(r)) for r in records]
    collection, report = assemble.assemble(results)
    outcome = validate.run(collection, country_iso2="CA")
    rows = [row for r in results for row in normalise.from_resolution(r)]

See docs/HARP_Design_v0_8_0.md for the stage model and the precision tiers.
"""

__version__ = "0.21.0"
__author__ = "NGIS"

from . import assemble, drop, identify, normalise, package, validate
from .cache import Cache
from .identify import Record, dedupe, load, shapes
from .resolution import (ATTRIBUTION, Attempt, IdShape, Klass, Resolution,
                         Tier)
from .router import Path, Supplier, choose, resolve, resolve_bc

__all__ = [
    "__version__", "ATTRIBUTION",
    # types
    "Record", "Resolution", "Attempt", "Tier", "Klass", "IdShape",
    "Path", "Supplier", "Cache",
    # stages
    "identify", "assemble", "validate", "normalise", "drop", "package",
    # functions
    "load", "shapes", "dedupe", "choose", "resolve", "resolve_bc",
]
