"""Path selection.

Every supplier resolves to exactly one of five acquisition paths. The router
reads the supplier register and decides which. Nothing else in HARP branches on
jurisdiction or land type - it all funnels through here.

    PATH                 JURISDICTION   INPUT                     eudr_sub_type
    ften_public          BC             timber mark               database_polygon
    supplier_geodata     any            polygons supplied direct  parcel
    detect_point         US             lat/long + window         change_detection_polygon
    detect_catchment     US             boundary + timeline       change_detection_polygon
    unresolved           -              nothing usable            -

Quality ranking, best to worst:
    supplier_geodata > ften_public > detect_point > detect_catchment

`unresolved` is a real outcome, not an error. A supplier who cannot provide any
of the four inputs is a commercial problem, and it should be visible in the
rejects file rather than silently absent.

ON land_type
------------
The register once carried `land_type` as an INPUT column. That was wrong and it
has been demoted to a hint. You cannot tell Crown from private by looking: FTEN
holds 83,916 timber marks beginning with E, and Harmac's sixteen E marks are
not among them. Only a query separates them, so `land_type` is something the
resolver establishes and writes down - see `resolution.Resolution.land_type`.

STATUS: `choose()` routes a supplier. `resolve_bc()` resolves a single
identifier through the BC ladder. US paths are stubs that fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Path(str, Enum):
    FTEN_PUBLIC = "ften_public"
    SUPPLIER_GEODATA = "supplier_geodata"
    DETECT_POINT = "detect_point"
    DETECT_CATCHMENT = "detect_catchment"
    UNRESOLVED = "unresolved"


SUB_TYPE = {
    Path.FTEN_PUBLIC: "database_polygon",
    Path.SUPPLIER_GEODATA: "parcel",
    Path.DETECT_POINT: "change_detection_polygon",
    Path.DETECT_CATCHMENT: "change_detection_polygon",
}

QUALITY = {
    Path.SUPPLIER_GEODATA: 1,
    Path.FTEN_PUBLIC: 2,
    Path.DETECT_POINT: 3,
    Path.DETECT_CATCHMENT: 4,
    Path.UNRESOLVED: 99,
}


@dataclass
class Supplier:
    """One row of the supplier register.

    Note the key is client number PLUS location code. Interfor holds eight
    registered locations and 11,069 blocks between them - matching on number
    alone silently drops most of a supplier's tenure.
    """
    supplier_id: str
    name: str
    jurisdiction: str                  # 'BC' | 'WA' | 'OR' | 'AK'
    land_type: str = ""                # HINT ONLY - resolved, not declared
    tier: str = "direct"               # 'direct' | 'indirect'
    client_number: str | None = None
    client_locations: list[str] | None = None
    geodata_format: str | None = None  # 'geojson' | 'shapefile' | None
    has_coordinates: bool = False
    has_catchment: bool = False
    contact: str | None = None
    notes: str | None = None


def choose(s: Supplier) -> Path:
    """Which acquisition path this supplier takes.

    Order matters - it is the quality ranking. We take the best evidence
    available, not the first that fits.
    """
    if s.geodata_format:
        return Path.SUPPLIER_GEODATA

    # A BC identifier is worth trying against the public register whatever the
    # register claims about land type - that claim is a hint, not a fact, and
    # the ladder establishes it properly.
    if s.jurisdiction == "BC" and (s.client_number or s.land_type != "private"):
        return Path.FTEN_PUBLIC

    if s.has_coordinates:
        return Path.DETECT_POINT

    if s.has_catchment:
        return Path.DETECT_CATCHMENT

    return Path.UNRESOLVED


def load_register(path: str) -> list[Supplier]:
    """Read the supplier register from CSV."""
    from . import io
    rows = io.read_csv_dicts(path)
    out = []
    for r in rows:
        locs = [x.strip() for x in (r.get("client_locations") or "").split("|") if x.strip()]
        out.append(Supplier(
            supplier_id=r.get("supplier_id", "").strip(),
            name=r.get("name", "").strip(),
            jurisdiction=r.get("jurisdiction", "").strip().upper(),
            land_type=r.get("land_type", "").strip().lower(),
            tier=r.get("tier", "direct").strip().lower(),
            client_number=(r.get("client_number") or "").strip() or None,
            client_locations=locs or None,
            geodata_format=(r.get("geodata_format") or "").strip() or None,
            has_coordinates=str(r.get("has_coordinates", "")).strip().lower()
                            in ("1", "true", "yes", "y"),
            has_catchment=str(r.get("has_catchment", "")).strip().lower()
                          in ("1", "true", "yes", "y"),
            contact=(r.get("contact") or "").strip() or None,
            notes=(r.get("notes") or "").strip() or None,
        ))
    return out


def summarise(suppliers: list[Supplier]) -> dict[str, int]:
    """How many suppliers on each path. Run this the moment the register lands."""
    counts: dict[str, int] = {}
    for s in suppliers:
        p = choose(s).value
        counts[p] = counts.get(p, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# ─────────────────────────── the BC ladder ─────────────────────────────────
#
# `choose()` above decides which path a SUPPLIER takes. What follows resolves a
# single IDENTIFIER within the ften_public path.
#
#   R1  FTEN 340  TIMBER_MARK                      -> P1
#   R2  FTEN 340  HARVEST_AUTH_FOREST_FILE_ID      -> P1
#   R3  FTEN 340  CUT_BLOCK_FOREST_FILE_ID         -> P1
#   R4  FTEN 340  file id + cutting permit         -> P1
#   R5  HBS       mark record                      -> classify, then branch
#   R5b private mark registry -> PID -> parcel      -> P3   (private only)
#   R6  FTEN 340  the licence HBS named            -> P2
#   R7  FTEN 340  client number + district         -> P2
#   R8  district ∩ private forest ownership        -> P3   (opt-in)
#   R9  district only                              -> P4
#
# First success wins. Every rung is recorded whether it hit or not, because
# "we checked six registers and it was not there" is a finding a client can
# act on and an empty result is not.
#
# R1 does the work. Across 217 Harmac identifiers every resolution - 71 of
# them - matched on TIMBER_MARK. Licence numbers, cutting-permit-shaped codes
# and alphanumeric marks all live in that one field. R2 to R4 have never fired
# and are kept as cheap insurance.

from . import identify as _identify                       # noqa: E402
from .identify import Record, shapes as _shapes          # noqa: E402
from .resolution import Klass, Resolution, Tier          # noqa: E402
from .sources import ften as _ften                       # noqa: E402
from .sources import hbs as _hbs                          # noqa: E402
from .sources import private_marks as _pm                 # noqa: E402

# What a class means when the ladder has been exhausted. These are the next
# step for a source that produced no geometry - not a reason to skip it.
#
# NOTHING IS FILTERED OUT IN ADVANCE
# ----------------------------------
# An earlier version short-circuited chip and yard sources on the grounds that
# they name a mill, not a harvest area. Most do. But the codes that turned out
# to matter most - the 0R1 suffixes, Mosaic's apostrophe codes, RYK's
# five-digit numbers - were all ones judged unpromising by eye and found to be
# real when finally tested. Every source now runs the full ladder and only
# falls back to a coarser answer once the finer ones have genuinely missed.
#
# The cost is queries, which are cheap and cached. The cost of the alternative
# is a lost cut block, discovered months later or not at all.
EXHAUSTED_NEXT_STEP = {
    Klass.C1: ("Custom processor running the client's own material. Resolve "
               "through the client's own log records."),
    Klass.C2: ("Third-party processor. Request their log purchase list, then "
               "re-classify each entry."),
    Klass.D: ("Aggregation point. Establish who supplied into it, then treat "
              "as C2."),
    Klass.NA: "Internal to the client. No external acquisition.",
}


def _blank(record: Record) -> Resolution:
    return Resolution(
        source_id=record.source_id, identifier=record.identifier,
        supplier_name=record.supplier_name, supplier_id=record.supplier_id,
        jurisdiction=record.jurisdiction, product_type=record.product_type,
        klass=record.klass, shapes=_shapes(record.identifier),
    )


def _hit(res: Resolution, rung: str, field: str, where: str,
         rows: list[dict], tier: Tier, fetch_geometry: bool, log=None) -> None:
    res.matched_rung, res.matched_field = rung, field
    res.registry = "FTEN cutblock 340"
    res.tier = tier
    res.path = Path.FTEN_PUBLIC.value
    res.log(rung, res.registry, where, True, "{} rows".format(len(rows)))
    first = rows[0] if rows else {}
    res.tenure_holder = (first.get("CLIENT_NAME") or "").strip()
    res.client_number = (first.get("CLIENT_NUMBER") or "").strip()
    res.client_location = (first.get("CLIENT_LOCATION_CODE") or "").strip()
    res.district_code = (first.get("GEOGRAPHIC_DISTRICT_CODE") or "").strip()
    res.district_name = (first.get("GEOGRAPHIC_DISTRICT_NAME") or "").strip()
    res.land_type = res.land_type or "public"
    if fetch_geometry:
        res.features = _ften.features(where, log=log or (lambda *_: None))
    else:
        res.features = [{"type": "Feature", "geometry": None,
                         "properties": r} for r in rows]


def resolve_bc(record: Record, hbs_client=None, fetch_geometry: bool = True,
               rule=None, log=None, catchment: bool = False,
               registry=None) -> Resolution:
    """Run one BC identifier down the ladder.

    `catchment` turns on R8. It is off by default because building a catchment
    costs three extra queries per private mark and the result is a bounded area
    rather than a harvest - useful, but not something to produce by accident.

    `registry` is a private_marks.Registry. When present it supplies R5b, which
    is the only route that gets a private mark down to a specific piece of land
    rather than a district.
    """
    res = _blank(record)
    uid = str(record.identifier or "").strip().upper()

    if not uid:
        res.unresolved_reason = "no identifier on the record"
        return res

    # Every source runs the full ladder. See the note on EXHAUSTED_NEXT_STEP.

    def window(rows):
        if not rule:
            return rows
        kept = [r for r in rows
                if _ften.in_window(r, getattr(rule, "start_after", None),
                                   getattr(rule, "start_before", None),
                                   getattr(rule, "require_end_date", False))]
        if rows and not kept:
            res.note("{} block(s) found but none inside the completion "
                     "window".format(len(rows)))
        return kept or rows

    # R1 to R3 - always tried in this order regardless of what the code looks
    # like. Shape-based routing skipped the field that held '61/243'.
    for rung, fld in (("R1", "TIMBER_MARK"),
                      ("R2", "HARVEST_AUTH_FOREST_FILE_ID"),
                      ("R3", "CUT_BLOCK_FOREST_FILE_ID")):
        rows, where, err = _ften.by_field(fld, uid)
        if rows:
            _hit(res, rung, fld, where, window(rows), Tier.P1A,
                 fetch_geometry, log)
            return res
        res.log(rung, "FTEN cutblock 340", where, False,
                "SERVICE ERROR: " + err if err else "")
        if err:
            # A miss and an outage are not the same thing. Continuing would
            # demote a cut block to a district envelope on the strength of a
            # network blip, and the result would look like data.
            res.note("Service failure on {} - result is not trustworthy, "
                     "re-run this source".format(rung))
            res.unresolved_reason = ("FTEN unreachable on {}: {}".format(rung, err))
            return res

    # R4 - file id and cutting permit together, never the permit alone
    if "/" in uid:
        fid, _, cp = uid.partition("/")
        rows, where, err = _ften.by_permit(fid, cp)
        if err:
            res.log("R4", "FTEN cutblock 340", where, False,
                    "SERVICE ERROR: " + err)
            res.note("Service failure on R4 - re-run this source")
            res.unresolved_reason = "FTEN unreachable on R4: " + err
            return res
        if rows:
            _hit(res, "R4", "file id + cutting permit", where, window(rows),
                 Tier.P1A, fetch_geometry, log)
            return res
        res.log("R4", "FTEN cutblock 340", where, False)

    # R5 - what is this thing?
    hbs_client = hbs_client or _hbs.Client()
    record_hbs = hbs_client.lookup(uid)
    if record_hbs is None:
        res.log("R5", "HBS", uid, False, "service unreachable")
        res.unresolved_reason = "HBS unreachable - retry before concluding"
        return res

    res.log("R5", "HBS", uid, bool(record_hbs.get("found")),
            (record_hbs.get("verdict") or "")
            + (" (cached)" if record_hbs.get("cached") else ""))
    res.raw_record_ref = record_hbs.get("raw_ref", "")

    if not record_hbs.get("found"):
        res.verdict = record_hbs.get("verdict") or "NOT FOUND"
        # The ladder is exhausted. What to do next depends on what this source
        # is: a mill that has no mark keeps its class and its next step, while
        # a log source whose identifier is not a mark is a data question.
        if record.klass in EXHAUSTED_NEXT_STEP:
            res.unresolved_reason = EXHAUSTED_NEXT_STEP[record.klass]
            res.note("Tried the register and HBS first - neither holds this "
                     "identifier, so the class stands")
        else:
            res.klass = Klass.E
            res.unresolved_reason = (
                record_hbs.get("note")
                or "not a timber mark - what does this field hold? "
                   "Client question.")
        return res

    res.verdict = record_hbs.get("verdict", "")
    res.verdict_basis = record_hbs.get("basis", "")
    res.tenure_holder = record_hbs.get("client_name", "")
    res.client_number = (record_hbs.get("client_no") or "").strip()
    res.district_code = record_hbs.get("district_code", "")
    res.district_name = record_hbs.get("district_name", "")
    res.region_code = record_hbs.get("region_code", "")
    if record_hbs.get("e_and_n"):
        res.note("Inside the E & N Land Belt - a mapped grant boundary that "
                 "narrows the catchment")

    if res.verdict.startswith("CROWN"):
        res.klass = Klass.A
        res.land_type = "public"

        # R6 - the licence HBS named
        licence = (record_hbs.get("licence") or "").strip().upper()
        # Sanity-check it. A licence is a short code; anything long is a
        # parse artefact and must not be sent to FTEN as a query.
        if len(licence) > 12 or " " in licence:
            res.note("HBS licence field did not look like a licence "
                     "({}...) - R6 skipped".format(licence[:24]))
            licence = ""
        if licence and licence != uid:
            for fld in ("HARVEST_AUTH_FOREST_FILE_ID", "TIMBER_MARK"):
                rows, where, err = _ften.by_field(fld, licence)
                if err:
                    res.log("R6", "FTEN cutblock 340", where, False,
                            "SERVICE ERROR: " + err)
                    continue
                if rows:
                    _hit(res, "R6", fld, where, window(rows), Tier.P2A,
                         fetch_geometry, log)
                    res.note("Resolved through the licence HBS reported, not "
                             "the mark")
                    return res
                res.log("R6", "FTEN cutblock 340", where, False)

        # R7 - keyed on client number, narrowed to the HBS district. Never by
        # name: the tenure holder is often a legacy entity the client has
        # never heard of.
        if res.client_number:
            unpadded = res.client_number.lstrip("0") or res.client_number
            for candidate in dict.fromkeys((res.client_number, unpadded)):
                for loc, _name in _ften.client_locations(candidate):
                    where = _ften.client_where(candidate, loc, res.district_code)
                    # paged: a holder query is not a handful of rows, and the
                    # single-page version silently reported a page size as a
                    # block count
                    rows = _ften.attributes_all(where, log=log)
                    if rows:
                        _hit(res, "R7", "CLIENT_NUMBER + district", where,
                             window(rows), Tier.P2A, fetch_geometry, log)
                        res.note("Holder's tenure in this district - an "
                                 "operating envelope, a superset of what was "
                                 "actually bought")
                        return res
                    res.log("R7", "FTEN cutblock 340", where, False)

        res.unresolved_reason = ("Recorded as Crown tenure but no geometry "
                                 "found under the mark, the licence or the "
                                 "holder")
        return res

    # PRIVATE - nothing in the Crown tenure system will ever hold this
    res.klass = Klass.B
    res.land_type = "private"
    res.path = Path.SUPPLIER_GEODATA.value

    # R5b - the private mark registry. The scaled-timbermark extracts link a
    # private mark to the parcels it was scaled from, and ParcelMap BC
    # publishes those parcels. This is the only route that gets a private mark
    # down to a specific piece of land rather than a district.
    #
    # The result is a SEARCH AREA. A parcel is the ownership boundary, and a
    # 200 ha parcel behind a 12 ha cut over-declares by sixteen times. The
    # harvest inside it is found by change detection - see harp.detect. Until
    # that runs, this is P3: bounded, real, and not a plot.
    if registry is not None:
        rec = registry.get(uid)
        if rec is None:
            res.log("R5b", "private mark registry", uid, False,
                    "mark not in the extracts")
        elif rec.route == "no_plot":
            res.log("R5b", "private mark registry", uid, False,
                    "blanket authority")
            res.note("Blanket authority - one mark covering a whole class of "
                     "land. No plot-level answer exists at any price.")
            res.unresolved_reason = (
                "Blanket authority: {}. This mark carries real scaled volume "
                "but no plot. Either the volume is excluded from what is "
                "declared, or the supplier's own harvest records are "
                "obtained.".format((rec.legal or rec.note or "")[:120]))
            return res
        elif not rec.resolvable:
            res.log("R5b", "private mark registry", uid, False, rec.route)
            res.note("In the registry but not free to resolve: "
                     + rec.route_note[:90])
        else:
            feats, missing = registry.parcels(uid)
            res.log("R5b", "ParcelMap BC via private mark registry",
                    "{} PID(s)".format(len(rec.pids)), bool(feats),
                    "{} parcels".format(len(feats)))
            if feats:
                res.features = feats
                res.tier = Tier.P1B
                res.registry = "ParcelMap BC (parcel) via BC scaled-timbermark extract"
                res.matched_rung = "R5b"
                res.matched_field = "PID"
                res.note("Parcel is the land the timber was scaled from - a "
                         "search area, not the harvest boundary. Run detection "
                         "inside it to get the cut.")
                if rec.inferred_pids:
                    res.note("{} PID(s) inferred from continuation shorthand - "
                             "resolving confirms the inference".format(
                                 len(rec.inferred_pids)))
                if missing:
                    res.note("{} PID(s) returned no parcel: {}".format(
                        len(missing), ", ".join(missing[:6])))
                if rec.route == "pmbc_plan":
                    res.note("Plan-number route - a plan covers every parcel "
                             "it created, so this is coarser than a PID match")
                res.unresolved_reason = ""
                return res
            res.note("In the registry with {} PID(s), but ParcelMap returned "
                     "nothing".format(len(rec.pids)))

    res.unresolved_reason = (
        "Private land. Geometry is held by {}. Request it, in {}.".format(
            res.tenure_holder or "the owner",
            res.district_name or res.district_code or "the issuing district"))

    # R8 - narrow the district to private forest land. The tier only improves
    # if the intersect actually narrows it, so the reduction is measured and
    # recorded rather than assumed.
    if res.district_code and catchment:
        feats, diag = _ften.private_catchment(res.district_code, log=log)
        res.log("R8", "FTEN ownership 238",
                "private ownership within {}".format(res.district_code),
                bool(feats), diag.get("error") or "{} polygons".format(
                    diag.get("features", 0)))
        if feats:
            res.features = feats
            res.tier = Tier.P1B
            res.registry = "FTEN ownership 238 within district 748"
            res.matched_rung = "R8"
            res.matched_field = diag.get("field_used", "")
            res.note("Catchment: private forest land within {}. Bounds where "
                     "the harvest could be, not where it was.".format(
                         res.district_name or res.district_code))
            if diag.get("truncated"):
                res.note(diag.get("note", "catchment truncated"))
            if diag.get("private_values"):
                res.note("Ownership classes used: "
                         + "; ".join(diag["private_values"][:6]))
            return res
        if diag.get("error"):
            res.note("Catchment not built: " + diag["error"])
    elif res.district_code:
        res.log("R8", "catchment", "district + private ownership", False,
                "not requested")

    if res.district_code:
        res.tier = Tier.P4
        res.note("District only. An operating area - never present it as a "
                 "plot.")
    return res


def resolve_stub(record: Record, note: str = "") -> Resolution:
    """A jurisdiction scoped but not built.

    Deliberately explicit. A source never attempted must not be mistaken for
    one attempted and missed.
    """
    res = _blank(record)
    res.log("--", record.jurisdiction or "unknown", record.identifier, False,
            "resolver not implemented")
    res.unresolved_reason = ("No resolver for {}. {}".format(
        record.jurisdiction or "this jurisdiction", note)).strip()
    return res


# Every US state is its own route: no federal equivalent of FTEN, no national
# timber mark, no identifier that crosses state lines.
STUBS = {
    "WA": ("WA DNR FPARS is public - shapefile, geodatabase, KML and some "
           "ArcGIS REST. Excludes applications older than 10 years and "
           "road-only activity. Expect two steps: get the mill's log purchase "
           "list, then resolve each entry."),
    "OR": ("ODF FERNS notification polygons, public ArcGIS FeatureServer at "
           "gis.odf.oregon.gov. Structurally closest to the BC pattern."),
    "AK": ("Tongass NF via USFS and the Alaska Geoportal. Native corporation, "
           "State and Mental Health Trust lands have no equivalent public "
           "register - lowest confidence of any jurisdiction met so far."),
    "CA": "CAL FIRE Timber Harvesting Plans. Not researched.",
}


def resolve(record: Record, hbs_client=None, fetch_geometry: bool = True,
            rule=None, log=None, catchment: bool = False,
            registry=None) -> Resolution:
    """Route one record to its jurisdiction's resolver."""
    jur = (record.jurisdiction or "").upper()
    if jur == "BC":
        return resolve_bc(record, hbs_client, fetch_geometry, rule, log,
                          catchment, registry)
    if jur in STUBS:
        return resolve_stub(record, STUBS[jur])
    return resolve_stub(record, "Unknown jurisdiction. Known: BC, "
                                + ", ".join(sorted(STUBS)))
