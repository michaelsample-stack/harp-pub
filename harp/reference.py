"""Where the reference data lives.

    from harp import reference
    path = reference.aliases(cfg)

Three files the pipeline reads on every run: the supplier alias table, mill
locations, and stated operating areas. They ship inside the package.

WHY INSIDE
----------
They are ours rather than the client's - a few hundred rows of decisions made
once and reviewed since - and they change rarely. Requiring somebody to point
at them each run is how a run quietly loses its mill locations and produces a
month of mill-buffer search areas without saying why.

Shipping them means a clone works, `pip install` carries them, and updating
one is a commit rather than an instruction.

THE ORDER THINGS ARE LOOKED FOR
-------------------------------
    1  whatever the caller passed in            an explicit choice wins
    2  `sources.reference.path` in config       a client with different data
    3  the working folder's data/registry       what a dev checkout already has
    4  the copy inside the package              always present

Three exists so an existing checkout keeps working unchanged: a file already
sitting in `data/registry` is used in preference to the packaged one, which is
what somebody editing it locally expects.
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGED = os.path.join(HERE, "reference")

ALIASES = "supplier_aliases.csv"
REGISTER = "supplier_register.csv"
MILLS = "supplier_locations.csv"
AREAS = "supplier_areas.csv"


def _configured(cfg) -> str:
    base = ((getattr(cfg, "sources", None) or {}).get("reference") or {}).get(
        "path")
    if not base:
        return ""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(base)))


def find(cfg, name: str, override: str = "") -> str:
    """The path to one reference file, or the packaged copy as a fallback.

    Always returns something. A caller that needs to know whether the file has
    any content should read it - an empty `supplier_areas.csv` is a normal
    state, not a missing file.
    """
    if override and os.path.isfile(override):
        return override

    base = _configured(cfg)
    if base:
        candidate = base if os.path.isfile(base) else os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate

    # A dev checkout that already has one of these keeps using it, so editing
    # it locally does what somebody editing it locally expects.
    local = os.path.join(os.getcwd(), "data", "registry", name)
    if os.path.isfile(local):
        return local

    return os.path.join(PACKAGED, name)


def register(cfg=None, override: str = "") -> str:
    """The supplier register - who has what, and who still needs an area.

    The one whose absence is quietest. Without it no supplier gets a search
    area at all, so a month resolves what it can from marks and produces
    nothing for the rest, with no line saying why.
    """
    return find(cfg, REGISTER, override)


def aliases(cfg=None, override: str = "") -> str:
    return find(cfg, ALIASES, override)


def mills(cfg=None, override: str = "") -> str:
    return find(cfg, MILLS, override)


def areas(cfg=None, override: str = "") -> str:
    return find(cfg, AREAS, override)


def describe(cfg=None) -> list:
    """Each file, where it came from, and how many rows it has.

    For the Setup tab and for a run's opening lines: a reference file silently
    resolving to an empty packaged copy is worth seeing before two hundred
    sources are resolved against it.
    """
    out = []
    for label, name, fn in (("supplier register", REGISTER, register),
                            ("supplier aliases", ALIASES, aliases),
                            ("mill locations", MILLS, mills),
                            ("stated areas", AREAS, areas)):
        path = fn(cfg)
        rows = 0
        try:
            with open(path, encoding="utf-8-sig") as fh:
                rows = max(0, sum(1 for _ in fh) - 1)
        except Exception:
            path = ""
        out.append({"label": label, "path": path, "rows": rows,
                    "packaged": path.startswith(PACKAGED)})
    return out
