"""Estimate what the services could not answer, from the nearest that could.

    from harp import gaps
    features, report = gaps.apply(features, cfg, log=log)

Some features come back from the detection service and the species rasters
with nothing. Across two real lot files that was about 7% for dates and under
1% for species. This estimates those from their neighbours so a month can go
out complete.

**On by default, and part of the pipeline rather than an option.** A
deliverable with blank fields is not a deliverable, and the services leave a
few percent unanswered on every month. This closes that.

What it is not is a way to paper over a bad month. Above a threshold - 15% of
features by default - the run says so loudly and marks the stage as needing
attention. A month where one feature in six had to be estimated is not a month
with a few gaps; it is a month where something upstream went wrong, and the
number is there so nobody has to notice it themselves.

HOW
---
For each feature with a gap, the nearest features that do have a value are
found and their values combined:

    dates      the median observation of the nearest neighbours, bracketed
               the same thirty days either side as a real one
    species    their composition, weighted by proximity, most common first

**Nearest by distance, not by supplier.** Two blocks a kilometre apart came
off the same forest whoever bought them; two blocks of one supplier two
hundred kilometres apart did not.

A neighbour beyond the radius is not a neighbour. Where nothing is close
enough the month's own median is used, and the basis says which of the two
happened.

WHY THE BASIS MATTERS MORE HERE THAN ANYWHERE
----------------------------------------------
Every other feature carries a basis naming where its value came from - a
register, a raster, a producer's own file. An estimate that looked like an
observation would be the only one that did not, and this is a regulatory
deliverable.

So every filled feature says so twice: on `harp_estimated`, and in the same
basis field the real ones use:

    estimated from the 5 nearest dated area(s), median 2026-03-15, all
    within 5.3 km. Not observed on this area.

`harp_estimated` is carried into the delivered view, so a month that contains
estimates can be told from one that does not after the `harp_` fields are
stripped.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, timedelta

# How many neighbours to draw on. Enough to smooth out one odd block, few
# enough that the estimate is still local.
NEIGHBOURS = 5

# Beyond this a feature is not a neighbour. Two blocks twenty kilometres apart
# may still be one operation; beyond that they are not.
MAX_KM = 20.0

# The same bracket a real date gets, so a filled feature reads like the rest.
BRACKET_DAYS = 30

# Below this share a species is noise rather than composition.
MIN_SHARE = 5.0


def settings(cfg) -> dict:
    g = ((getattr(cfg, "sources", None) or {}).get("gaps") or {})
    return {
        # On. The alternative is a deliverable with blank fields, and the
        # services leave a few percent unanswered every month.
        "enabled": g.get("enabled", True),
        "neighbours": int(g.get("neighbours", NEIGHBOURS)),
        "max_km": float(g.get("max_km", MAX_KM)),
        "dates": g.get("dates", True),
        "species": g.get("species", True),
        # Above this share of features, estimating is no longer filling gaps -
        # it is covering for something that went wrong earlier.
        "warn_above": float(g.get("warn_above", 0.15)),
    }


# ─────────────────────────────── geometry ──────────────────────────────────

def centroid(feature: dict):
    """Roughly where a feature is, in degrees."""
    coords = (feature.get("geometry") or {}).get("coordinates")
    if not coords:
        return None
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for part in c:
                walk(part)

    try:
        walk(coords)
    except Exception:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else None


def km_between(a, b) -> float:
    """Great-circle distance, near enough for choosing neighbours."""
    lon1, lat1 = a
    lon2, lat2 = b
    p = math.pi / 180.0
    x = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(max(0.0, x)))


def neighbours(point, donors: list, k: int, max_km: float) -> list:
    """The k nearest donors, and how far away each is."""
    out = []
    for idx, centre, payload in donors:
        d = km_between(point, centre)
        if d <= max_km:
            out.append((d, idx, payload))
    out.sort(key=lambda t: t[0])
    return out[:k]


# ──────────────────────────────── dates ────────────────────────────────────

def _iso(value) -> str:
    return str(value or "").strip()[:10]


def middle_date(dates: list) -> str:
    """The median of some dates.

    Median rather than mean: one block cut months before the rest should not
    drag the estimate, and either middle value is a date somebody observed.
    """
    days = []
    for d in dates:
        try:
            days.append(date.fromisoformat(d).toordinal())
        except ValueError:
            continue
    if not days:
        return ""
    days.sort()
    return date.fromordinal(days[len(days) // 2]).isoformat()


def bracket(observed: str) -> tuple:
    try:
        d = date.fromisoformat(observed)
    except ValueError:
        return "", ""
    return ((d - timedelta(days=BRACKET_DAYS)).isoformat(),
            (d + timedelta(days=BRACKET_DAYS)).isoformat())


def unbracket(start: str) -> str:
    """The observation a bracketed start date was built from.

    Averaging start dates and re-bracketing would widen the window every time
    it was done, so the estimate works from the observations.
    """
    try:
        return (date.fromisoformat(_iso(start))
                + timedelta(days=BRACKET_DAYS)).isoformat()
    except ValueError:
        return ""


def _mark(props: dict, what: str) -> None:
    prev = str(props.get("harp_estimated") or "")
    other = "species" if what == "dates" else "dates"
    props["harp_estimated"] = ("dates and species" if other in prev
                               else what)


def fill_dates(features: list[dict], k: int, max_km: float,
               log=print) -> dict:
    donors, holes = [], []
    for i, f in enumerate(features):
        c = centroid(f)
        if c is None:
            continue
        p = f.get("properties") or {}
        start = _iso(p.get("HarvestStartDate"))
        if start:
            seen = unbracket(start)
            if seen:
                donors.append((i, c, seen))
        else:
            holes.append((i, c))

    if not holes:
        log("  every feature already has dates")
        return {"filled": 0}
    if not donors:
        log("  no feature has a date, so there is nothing to estimate from")
        return {"filled": 0, "why": "no donors"}

    log("  {:,} gap(s), {:,} feature(s) to draw on".format(len(holes),
                                                           len(donors)))
    whole = middle_date([d for _i, _c, d in donors])
    log("  the month's own median observation is {}".format(whole))

    filled, far, dists = 0, 0, []
    for idx, point in holes:
        near = neighbours(point, donors, k, max_km)
        if near:
            seen = middle_date([d for _dist, _i, d in near])
            furthest = near[-1][0]
            dists.append(furthest)
            note = ("estimated from the {} nearest dated area(s), median {}, "
                    "all within {:.1f} km. Not observed on this area."
                    .format(len(near), seen, furthest))
        else:
            # Nothing close enough. The month's median is a weaker answer and
            # says so rather than pretending to be local.
            seen = whole
            far += 1
            note = ("estimated from the median of every dated area in this "
                    "month, {}, because nothing lay within {:.0f} km. Not "
                    "observed on this area.".format(seen, max_km))
        a, b = bracket(seen)
        if not a:
            continue
        p = features[idx].setdefault("properties", {})
        p["HarvestStartDate"], p["HarvestEndDate"] = a, b
        p["harp_harvest_basis"] = note
        _mark(p, "dates")
        filled += 1

    log("  {:,} filled".format(filled))
    if dists:
        log("  neighbours a median of {:.1f} km away".format(
            statistics.median(dists)))
    if far:
        log("  {:,} had nothing within {:.0f} km and used the month "
            "median".format(far, max_km))
    return {"filled": filled, "far": far}


# ─────────────────────────────── species ───────────────────────────────────

def blend(mixes: list) -> tuple:
    """Several compositions as one, weighted by how near each came from."""
    weighted = {}
    for weight, entries in mixes:
        for sp in entries:
            name = sp.get("CommonName")
            if not name:
                continue
            try:
                pct = float(sp.get("ProportionPct") or 0)
            except (TypeError, ValueError):
                continue
            if pct <= 0:
                continue
            key = (name, sp.get("Genus", ""), sp.get("Species", ""),
                   sp.get("HSCode", ""))
            weighted[key] = weighted.get(key, 0.0) + weight * pct
    if not weighted:
        return "", "", []

    total = sum(weighted.values())
    pcts = {k: v / total * 100.0 for k, v in weighted.items()}
    kept = {k: v for k, v in pcts.items() if v >= MIN_SHARE}
    if not kept:
        kept = dict(sorted(pcts.items(), key=lambda kv: -kv[1])[:1])
    kept_total = sum(kept.values())
    ordered = sorted(kept.items(), key=lambda kv: -kv[1])

    parts, structured = [], []
    for (name, genus, sp, hs), pct in ordered:
        share = round(pct / kept_total * 100.0, 1)
        parts.append("{} {:.0f}%".format(name, share))
        structured.append({"CommonName": name, "Genus": genus, "Species": sp,
                           "HSCode": hs, "ProportionPct": share})
    return ordered[0][0][0], "; ".join(parts), structured


def fill_species(features: list[dict], k: int, max_km: float,
                 log=print) -> dict:
    donors, holes = [], []
    for i, f in enumerate(features):
        c = centroid(f)
        if c is None:
            continue
        p = f.get("properties") or {}
        raw = str(p.get("harp_species_json") or "").strip()
        if raw and str(p.get("harp_species") or "").strip():
            try:
                donors.append((i, c, json.loads(raw)))
            except ValueError:
                pass
        elif not str(p.get("harp_species") or "").strip():
            holes.append((i, c))

    if not holes:
        log("  every feature already has species")
        return {"filled": 0}
    if not donors:
        log("  no feature has species, so there is nothing to estimate from")
        return {"filled": 0, "why": "no donors"}

    log("  {:,} gap(s), {:,} feature(s) to draw on".format(len(holes),
                                                           len(donors)))
    whole = blend([(1.0, entries) for _i, _c, entries in donors])

    filled, far, dists = 0, 0, []
    for idx, point in holes:
        near = neighbours(point, donors, k, max_km)
        if near:
            # A neighbour half a kilometre away should count for more than one
            # nineteen kilometres off. Inverse distance, floored so the very
            # nearest does not swamp the rest.
            dominant, readable, structured = blend(
                [(1.0 / max(0.5, dist), entries)
                 for dist, _i, entries in near])
            furthest = near[-1][0]
            dists.append(furthest)
            note = ("estimated from the {} nearest area(s) with species, all "
                    "within {:.1f} km, weighted by distance. Not read from "
                    "the raster over this area.".format(len(near), furthest))
        else:
            dominant, readable, structured = whole
            far += 1
            note = ("estimated from every area with species in this month, "
                    "because nothing lay within {:.0f} km. Not read from the "
                    "raster over this area.".format(max_km))
        if not readable:
            continue
        p = features[idx].setdefault("properties", {})
        p["harp_species_dominant"] = dominant
        p["harp_species"] = readable
        p["harp_species_json"] = json.dumps(structured)
        p["harp_species_basis"] = note
        _mark(p, "species")
        filled += 1

    log("  {:,} filled".format(filled))
    if dists:
        log("  neighbours a median of {:.1f} km away".format(
            statistics.median(dists)))
    if far:
        log("  {:,} had nothing within {:.0f} km and used the whole "
            "month".format(far, max_km))
    return {"filled": filled, "far": far}


# ──────────────────────────────── the stage ────────────────────────────────

def apply(features: list[dict], cfg, log=print) -> tuple:
    """Fill whatever the earlier stages could not answer."""
    opts = settings(cfg)
    if not opts["enabled"]:
        # Switched off deliberately. Worth saying what that means rather than
        # passing silently, because the month will go out with blank fields.
        log("filling gaps is switched off in config. Features the services "
            "could not answer keep their empty fields, and their basis says "
            "why.")
        return features, {"enabled": False}

    before_d = sum(1 for f in features
                   if _iso((f.get("properties") or {}).get(
                       "HarvestStartDate")))
    before_s = sum(1 for f in features
                   if str((f.get("properties") or {}).get("harp_species")
                          or "").strip())

    report = {"enabled": True}
    if opts["dates"]:
        log("dates")
        report["dates"] = fill_dates(features, opts["neighbours"],
                                     opts["max_km"], log=log)
    if opts["species"]:
        log("")
        log("species")
        report["species"] = fill_species(features, opts["neighbours"],
                                         opts["max_km"], log=log)

    after_d = sum(1 for f in features
                  if _iso((f.get("properties") or {}).get(
                      "HarvestStartDate")))
    after_s = sum(1 for f in features
                  if str((f.get("properties") or {}).get("harp_species")
                         or "").strip())
    est = sum(1 for f in features
              if str((f.get("properties") or {}).get("harp_estimated")
                     or "").strip())

    log("")
    log("{:,} have dates ({:+,}), {:,} have species ({:+,})".format(
        after_d, after_d - before_d, after_s, after_s - before_s))
    log("{:,} feature(s) carry an estimated value, marked on "
        "harp_estimated".format(est))

    total = len(features) or 1
    share = est / total
    report["estimated"] = est
    report["share"] = share
    report["over_threshold"] = share > opts["warn_above"]

    if report["over_threshold"]:
        # Loud, because the alternative is a month that looks complete and is
        # a third guesswork. The threshold is not a rule about what is
        # acceptable; it is the point at which somebody should look.
        log("")
        log("!" * 66)
        log("{:.0f}% of this month had to be estimated, over the {:.0f}% "
            "mark.".format(share * 100, opts["warn_above"] * 100))
        log("")
        log("That is not a few gaps. Something upstream did not answer:")
        log("  - the detection window may not cover when this wood was cut")
        log("  - the detection record has a floor and nothing before it "
            "exists to be found")
        log("  - a jurisdiction may be unset, sending features to the wrong "
            "raster")
        log("")
        log("The month is complete and every estimate is marked, but it "
            "should be looked at before it goes anywhere.")
        log("!" * 66)
    elif est:
        log("  {:.1f}% of the month, within the {:.0f}% mark".format(
            share * 100, opts["warn_above"] * 100))

    return features, report
