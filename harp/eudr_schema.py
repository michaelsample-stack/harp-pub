"""The EUDR view of a month.

    from harp import eudr_schema
    view, report = eudr_schema.project(month_features)

TWO FUNCTIONS, AND THE ORDER MATTERS
------------------------------------
    add(features)      the four EUDR fields alongside everything already there
    project(features)  only the four, for a customer

**`add` runs before validation. `project` runs at delivery.**

The validator only inspects the four named fields - the blank check and the
capitalisation check both work from a known list, and anything else is ignored.
So a month can carry its `harp_` fields all the way through validation,
cleaning and into the library, and only be stripped at the point somebody
outside sees it.

That ordering is not cosmetic. The library month is what a production lot is
resolved against, and that selection is made on `harp_supplier`. Strip early
and lot resolution has nothing to match on.

    ProducerName      who cut the wood
    ProducerCountry   the ISO 3166-1 alpha-2 country it was cut in
    ProductionPlace   what identifies the place - a timber mark, or an area
    Area              hectares

THE RULE THAT SHAPES EVERYTHING HERE
------------------------------------
Under the validation taxonomy, a **missing** field is Recommended and a
**blank** field is Required:

    1.1.12  Missing ProducerName      Recommended
    1.1.7   Blank required fields     Required

So `"ProducerName": ""` is worse than no key at all. Every field here is
omitted when it has no value, never emitted empty. That is the opposite of how
the rest of the pipeline fills fields, and it is deliberate.

Capitalisation is Required too (1.1.9), which is why `ProducerName` is carried
in EUDR casing from the point of resolution rather than renamed at the end.

AREA
----
Measured from the geometry being shipped, so that the declared area and the
computed area agree - a mismatch is Required (1.2.10).

Never carried from a parent. A detection inside a two hundred hectare tenure
block is forty hectares, not two hundred, and the whole point of the round trip
is that difference.

A point is the exception. It has no area to measure, so the area the detection
service stated is the only figure available and it is used as given. Those run
1.00 to 3.99 ha, which keeps them under the four hectare ceiling that makes an
oversized point a Required failure (1.1.16).

PRODUCERCOUNTRY
---------------
From the jurisdiction, and there is a trap in it. Our `CA` means California,
whose country is `US`. Only `BC` maps to `CA`. Getting that backwards would
file Californian harvest as Canadian, and nothing downstream would notice.
"""

from __future__ import annotations

from collections import Counter

# Jurisdiction to ISO 3166-1 alpha-2.
#
# Note CA. In this pipeline it is California - the state - and its country is
# US. Canada is reached only from BC. The two are never conflated because they
# arrive in different fields, but a reader of this table deserves the warning.
COUNTRY = {
    "BC": "CA", "BRITISH COLUMBIA": "CA", "CANADA": "CA",
    "WA": "US", "WASHINGTON": "US",
    "OR": "US", "OREGON": "US",
    "CA": "US", "CALIFORNIA": "US",     # California, not Canada
    "AK": "US", "ALASKA": "US",
    "ID": "US", "IDAHO": "US",
    "MT": "US", "MONTANA": "US",
    "US": "US", "USA": "US",
}

# The four, in the order a reader expects them.
FIELDS = ("ProducerName", "ProducerCountry", "ProductionPlace", "Area")


def country_of(jurisdiction: str) -> str:
    """ISO2 for a jurisdiction, or empty if it is not one we know."""
    j = str(jurisdiction or "").strip().upper()
    if not j:
        return ""
    if j in COUNTRY:
        return COUNTRY[j]
    # "BC, WA" and similar - take the first that resolves, and only if the
    # rest agree. A source spanning two countries has no single answer and is
    # better left empty than guessed.
    parts = [p.strip() for p in j.replace("/", ",").split(",") if p.strip()]
    found = {COUNTRY[p] for p in parts if p in COUNTRY}
    return found.pop() if len(found) == 1 else ""


def place_of(props: dict) -> str:
    """What identifies where this was cut.

    Best first: the timber mark, which names a specific harvest; then whatever
    the area was called; then the district. A detection carries whichever of
    these its parent area could give it.
    """
    for key in ("harp_timber_mark", "harp_key_name", "harp_key",
                "harp_district"):
        v = str(props.get(key) or "").strip()
        # A key that is only a client number identifies a company, not a
        # place, so it is skipped rather than shipped as one.
        if v and not (key == "harp_key" and v.isdigit()):
            return v
    return ""


def _polygon_area_ha(geom) -> float:
    """Hectares on the ellipsoid, not in degrees."""
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        g = Geod(ellps="WGS84")
        s = shape(geom)
        if s.geom_type == "Polygon":
            polys = [s]
        elif s.geom_type == "MultiPolygon":
            polys = list(s.geoms)
        else:
            return 0.0
        return sum(abs(g.geometry_area_perimeter(p)[0]) for p in polys) / 10000.0
    except Exception:
        return 0.0


def area_of(feature: dict) -> tuple[float, str]:
    """Hectares, and how they were arrived at.

    Returns (area, basis). A polygon is measured. A point cannot be, so the
    area the detection service stated is used - it is the only figure there is,
    and it is what the point stands for.
    """
    geom = feature.get("geometry") or {}
    gtype = str(geom.get("type") or "")
    props = feature.get("properties") or {}

    if gtype in ("Polygon", "MultiPolygon"):
        measured = _polygon_area_ha(geom)
        if measured > 0:
            return round(measured, 4), "measured"
        # shapely or pyproj missing, or a geometry that would not build. Fall
        # through to the stated figure rather than shipping a zero, but say so.
        stated = _stated(props)
        return (stated, "stated, geometry could not be measured") if stated \
            else (0.0, "none")

    if gtype in ("Point", "MultiPoint"):
        stated = _stated(props)
        return (stated, "stated") if stated else (0.0, "none")

    return 0.0, "none"


def _stated(props: dict) -> float:
    for key in ("harp_area_ha", "area_ha", "Area"):
        try:
            v = float(props.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return round(v, 4)
    return 0.0


def project_feature(feature: dict) -> tuple[dict, list]:
    """One feature as EUDR sees it. Returns (feature, fields omitted)."""
    props = feature.get("properties") or {}
    out, missing = {}, []

    name = str(props.get("ProducerName") or "").strip()
    if name:
        out["ProducerName"] = name
    else:
        missing.append("ProducerName")

    # A producer's own file states the country. Believe it over anything we
    # would derive - they know where they cut.
    stated = str(props.get("ProducerCountry") or "").strip().upper()
    country = stated if len(stated) == 2 else country_of(
        props.get("harp_jurisdiction"))
    if country:
        out["ProducerCountry"] = country
    else:
        missing.append("ProducerCountry")

    place = place_of(props)
    if place:
        out["ProductionPlace"] = place
    else:
        missing.append("ProductionPlace")

    area, _basis = area_of(feature)
    if area > 0:
        # A number, not a string. A string here is a Required failure on data
        # type (1.1.8).
        out["Area"] = area
    else:
        missing.append("Area")

    return {"type": "Feature", "geometry": feature.get("geometry"),
            "properties": out}, missing


def add(features: list[dict], log=print) -> tuple[list[dict], dict]:
    """The four EUDR fields, alongside everything already on the feature.

    This is what goes into validation and into the library. Nothing is
    removed - the month keeps its `harp_` fields, because a production lot is
    resolved against them later.
    """
    out, missing, points = [], Counter(), 0
    for f in features:
        projected, gaps = project_feature(f)
        props = dict(f.get("properties") or {})
        props.update(projected["properties"])
        # A field that could not be filled is removed rather than left blank,
        # even here - a blank one fails validation where a missing one only
        # warns.
        for g in gaps:
            props.pop(g, None)
        out.append({"type": "Feature", "geometry": f.get("geometry"),
                    "properties": props})
        for g in gaps:
            missing[g] += 1
        if str((f.get("geometry") or {}).get("type", "")).endswith("Point"):
            points += 1

    total = len(out)
    log("{:,} feature(s) given their EUDR fields".format(total))
    if points:
        log("  {:,} point(s) - area taken as stated, since a point has none "
            "to measure".format(points))
    if missing:
        log("  omitted where nothing filled them: " + ", ".join(
            "{} on {:,}".format(k, n) for k, n in missing.most_common()))
        for field, n in missing.items():
            if total and n == total:
                log("    {} is missing from every feature. Nothing upstream "
                    "sets it.".format(field))
    return out, {"features": total, "missing": dict(missing),
                 "points": points}


def project(features: list[dict], log=print) -> tuple[dict, dict]:
    """Only the four fields. For a customer, at the point of delivery.

    Returns (collection, report). The report says what could not be filled,
    because a field omitted from every feature is a gap in the pipeline rather
    than a property of the data.
    """
    out, missing, bases, countries = [], Counter(), Counter(), Counter()
    points = 0

    for f in features:
        projected, gaps = project_feature(f)
        out.append(projected)
        for g in gaps:
            missing[g] += 1
        _a, basis = area_of(f)
        bases[basis] += 1
        c = projected["properties"].get("ProducerCountry")
        if c:
            countries[c] += 1
        if str((f.get("geometry") or {}).get("type", "")).endswith("Point"):
            points += 1

    total = len(out)
    log("{:,} feature(s) projected".format(total))
    if countries:
        log("  " + ", ".join("{:,} {}".format(n, c)
                             for c, n in countries.most_common()))
    if points:
        log("  {:,} point(s) - area taken as stated, since a point has none "
            "to measure".format(points))

    if missing:
        log("")
        log("  fields omitted, because a blank field fails where a missing "
            "one only warns:")
        for field, n in missing.most_common():
            log("    {:<18}{:>7,}  ({:.0f}% of features)".format(
                field, n, n / total * 100 if total else 0))
        for field, n in missing.items():
            if total and n == total:
                # Nothing filled it anywhere. That is not the data being thin,
                # it is a field nothing in the pipeline populates.
                log("")
                log("    {} is missing from every feature. Nothing upstream "
                    "sets it.".format(field))

    report = {"features": total, "missing": dict(missing),
              "area_basis": dict(bases), "countries": dict(countries),
              "points": points}

    return ({"type": "FeatureCollection", "name": "harp_eudr",
             "features": out}, report)


def summary(report: dict) -> str:
    lines = ["{:,} feature(s)".format(report.get("features", 0))]
    for field, n in sorted((report.get("missing") or {}).items(),
                           key=lambda kv: -kv[1]):
        lines.append("  {} omitted on {:,}".format(field, n))
    return "\n".join(lines)
