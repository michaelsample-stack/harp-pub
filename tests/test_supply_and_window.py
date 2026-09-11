"""The delivery-driven month, and the window it searches.

These two things went wrong together and neither showed up: a missing import
and a variable used before it existed meant every run fell back to resolving
the whole register, and the exception handler that caught them said only that
the delivery record could not be read - which sounded like the client's fault.

So these tests do not check output values. They check that the path runs at
all, on the real modules, with nothing stubbed but the network.
"""

from __future__ import annotations

import pytest

from harp import run as run_stage
from harp import supply


# ─────────────────────────────── the window ────────────────────────────────

def test_window_reaches_back_and_ends_at_the_month():
    """A May run searches March to May and declares for May.

    A chip delivered in May came from a log cut before May, so searching only
    May finds harvest that has not been delivered and misses what fed the
    month. The window still ends at the month's end rather than shifting
    wholesale - some of what a month delivers really was cut within it.
    """
    start, end = run_stage._window("2026-05", 2)
    assert start == "2026-03-01"
    assert end == "2026-05-31"


def test_window_crosses_the_year():
    start, end = run_stage._window("2026-01", 2)
    assert start == "2025-11-01"
    assert end == "2026-01-31"


def test_window_handles_a_short_month():
    _start, end = run_stage._window("2026-02", 2)
    assert end == "2026-02-28"


def test_window_with_no_lag_is_the_month_itself():
    assert run_stage._window("2026-05", 0) == ("2026-05-01", "2026-05-31")


def test_window_refuses_nonsense():
    assert run_stage._window("not a month") == ("", "")


# ─────────────────────── the delivery-driven month ─────────────────────────

REGISTER = [
    {"SOURCEID": "COS-A1", "UNITID": "A81234", "SUPPID": "COS",
     "STATEID": "BC", "PRODUCT_TYPE": "LOG", "ORIGIN_TYPE": "PURCHASE",
     "NAME": "Direct Delivery MIDISL"},
    {"SOURCEID": "MIDISL-QUALICUM", "UNITID": "QUALICUM", "SUPPID": "MIDISL",
     "STATEID": "BC", "PRODUCT_TYPE": "BULK", "ORIGIN_TYPE": "YARD",
     "NAME": "Mid Island Fibre"},
    {"SOURCEID": "SIERRA-BURLINGTON", "UNITID": "BURLINGTON",
     "SUPPID": "SIERRA", "STATEID": "WA", "PRODUCT_TYPE": "BULK",
     "ORIGIN_TYPE": "PURCHASE", "NAME": "Sierra Pacific"},
    {"SOURCEID": "HARMAC-YARD", "UNITID": "YARD", "SUPPID": "HARMAC",
     "STATEID": "BC", "PRODUCT_TYPE": "BULK", "ORIGIN_TYPE": "YARD",
     "NAME": "Harmac Pacific yard"},
    # In the register and delivering nothing this month. The reason the
    # delivery record decides scope rather than the register.
    {"SOURCEID": "DORMANT-1", "UNITID": "Z99999", "SUPPID": "DORM",
     "STATEID": "BC", "PRODUCT_TYPE": "LOG", "ORIGIN_TYPE": "PURCHASE",
     "NAME": "Direct Delivery DCT"},
]

LOADS = [
    {"source": "MIDISL-QUALICUM", "bdt": 4000.0, "date": "2026-05-04"},
    {"source": "MIDISL-QUALICUM", "bdt": 3365.0, "date": "2026-05-19"},
    {"source": "SIERRA-BURLINGTON", "bdt": 7512.0, "date": "2026-05-11"},
    {"source": "HARMAC-YARD", "bdt": 120.0, "date": "2026-05-02"},
]


def test_the_month_is_what_arrived():
    plan, report = supply.plan_month(LOADS, REGISTER, "2026-05",
                                     log=lambda *_a: None)
    delivered = {e["source_id"] for e in plan}
    assert "DORMANT-1" not in delivered, \
        "a source that delivered nothing is not part of the month"
    assert report["sources"] == 3
    assert abs(report["bdt"] - 14997.0) < 0.01


def test_arrivals_are_sorted_by_kind():
    plan, _ = supply.plan_month(LOADS, REGISTER, "2026-05",
                                log=lambda *_a: None)
    kind = {e["source_id"]: e["kind"] for e in plan}
    assert kind["MIDISL-QUALICUM"] == supply.TOLL
    assert kind["SIERRA-BURLINGTON"] == supply.MERCHANT
    assert kind["HARMAC-YARD"] == supply.YARD


def test_a_toll_chipper_gets_the_marks_routed_to_it():
    """The register records where each log purchase was sent, and that
    routing is the only link between a log purchase and the chips that come
    back from it."""
    plan, _ = supply.plan_month(LOADS, REGISTER, "2026-05",
                                log=lambda *_a: None)
    toll = next(e for e in plan if e["kind"] == supply.TOLL)
    assert "A81234" in toll["pool"]


def test_own_yard_material_is_not_resolved():
    """It arrived, but not from a forest this month, and declaring it would
    double count the wood that did."""
    plan, _ = supply.plan_month(LOADS, REGISTER, "2026-05",
                                log=lambda *_a: None)
    ids, pooled, _none = supply.to_records(plan, log=lambda *_a: None)
    assert all(e["source_id"] != "HARMAC-YARD" for e in ids)
    assert pooled, "the toll chipper should have produced a pool"


def test_a_nan_tonnage_does_not_poison_the_totals():
    """One NaN survives float() and turns every total and share into nan."""
    loads = LOADS + [{"source": "SIERRA-BURLINGTON", "bdt": float("nan"),
                      "date": "2026-05-20"}]
    _plan, report = supply.plan_month(loads, REGISTER, "2026-05",
                                      log=lambda *_a: None)
    assert report["bdt"] == report["bdt"], "the total came out as nan"
    for share in report["shares"].values():
        assert share == share


def test_the_delivery_path_has_what_it_needs():
    """The modules the delivery-driven path calls are actually imported.

    A missing import here meant every month silently fell back to resolving
    the whole register, and the handler reported it as an unreadable delivery
    record.
    """
    assert hasattr(run_stage, "lots_stage")
    assert hasattr(run_stage, "supply_stage")
    assert hasattr(run_stage.lots_stage, "read_deliveries")
    assert hasattr(run_stage.supply_stage, "plan_month")


# ─────────────────── one detection, one feature ────────────────────────────

def _square(x, y, s=0.01):
    return {"type": "Polygon",
            "coordinates": [[[x, y], [x + s, y], [x + s, y + s],
                             [x, y + s], [x, y]]]}


def test_a_detection_appears_once_at_its_strongest_tier():
    """A detection inside a registered block is usually inside a district too.

    Emitting both put the same ground in the month twice, making two claims
    of different strength and sometimes naming two different suppliers.
    """
    from harp import detect
    geom = _square(-124.9, 49.6)
    strong = [{"type": "Feature", "geometry": geom, "properties": {
        "harp_tier": "P2b", "harp_supplier": "DCT",
        "harp_timber_mark": "GR2106", "harp_detected_first": "2026-04-15"}}]
    weak = [{"type": "Feature", "geometry": geom, "properties": {
        "harp_tier": "P3b", "harp_supplier": "Coastland",
        "harp_detected_first": "2026-04-15"}}]
    out = detect.merge([], strong, weak)
    assert len(out) == 1
    kept = out[0]["properties"]
    assert kept["harp_tier"] == "P2b"
    assert kept["harp_timber_mark"] == "GR2106"


def test_a_detection_found_only_in_a_district_is_kept():
    from harp import detect
    weak = [{"type": "Feature", "geometry": _square(-120, 52), "properties": {
        "harp_tier": "P3b", "harp_supplier": "Aspen",
        "harp_detected_first": "2026-04-20"}}]
    assert len(detect.merge([], [], weak)) == 1


def test_resolved_harvest_is_not_deduplicated_against_detections():
    """Things resolved from an identifier are not detections and do not
    compete with them - two blocks can legitimately share a boundary."""
    from harp import detect
    harvest = [{"type": "Feature", "geometry": _square(-124.9, 49.6),
                "properties": {"harp_tier": "P1a", "harp_supplier": "A"}},
               {"type": "Feature", "geometry": _square(-124.9, 49.6),
                "properties": {"harp_tier": "P1a", "harp_supplier": "B"}}]
    assert len(detect.merge(harvest, [], [])) == 2


# ───────────────── the four fields identify something ──────────────────────

def test_production_place_identifies_a_harvest_not_a_district():
    """It named the area, so every harvest in one district carried the same
    string - two and a half thousand features called "Campbell River"."""
    from harp import eudr_schema
    feats = [{"type": "Feature", "geometry": _square(-125 + i * 0.02, 49.5),
              "properties": {"harp_tier": "P3b", "harp_key": "DCR",
                             "harp_key_name": "Campbell River",
                             "ProducerName": "Coastland",
                             "harp_jurisdiction": "BC", "harp_area_ha": 30.0}}
             for i in range(3)]
    out, _ = eudr_schema.add(feats, month="2026-05", log=lambda *_a: None)
    places = [f["properties"]["ProductionPlace"] for f in out]
    assert len(set(places)) == 3, "each harvest needs its own place"
    assert places[0].startswith("DCR-202605-")


def test_a_timber_mark_is_used_as_the_place_unchanged():
    """A mark already names a specific harvest and needs nothing added."""
    from harp import eudr_schema
    feats = [{"type": "Feature", "geometry": _square(-120, 52), "properties": {
        "harp_tier": "P2b", "harp_timber_mark": "GR2106",
        "harp_key_name": "Campbell River", "ProducerName": "DCT",
        "harp_jurisdiction": "BC", "harp_area_ha": 40.0}}]
    out, _ = eudr_schema.add(feats, month="2026-05", log=lambda *_a: None)
    assert out[0]["properties"]["ProductionPlace"] == "GR2106"


def test_a_bare_supplier_code_is_named_rather_than_left_blank():
    """The client buys under codes it has not explained. Naming the code says
    what is missing and who can supply it; a blank says nothing."""
    from harp import catchments
    name, why = catchments._producer_of({"code": "RYK"}, "RYK")
    assert "RYK" in name
    assert "not provided" in name
    assert "purchasing code" in why


def test_a_real_name_still_wins_over_the_placeholder():
    from harp import catchments
    name, _why = catchments._producer_of(
        {"code": "COS", "supplier_name": "Coastland Wood Ind."},
        "Coastland Wood Ind.")
    assert name == "Coastland Wood Ind."


# ───────────────────────── log deliveries ──────────────────────────────────

def _write_csv(tmp_path, name, rows):
    import csv
    p = tmp_path / name
    with open(p, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return str(p)


def test_a_hand_made_spreadsheet_reads(tmp_path):
    """More than one format will arrive for the same three facts, so the
    reader looks for the facts rather than the layout."""
    from harp import log_deliveries
    p = _write_csv(tmp_path, "marks.csv", [
        ["Supplier", "Timber Mark", "Date Received", "Net m3"],
        ["Cape Mudge", "GR2106", "12/08/2026", "1,240.5"],
        ["Cape Mudge", "GR2107", "2026-08-20", "880.25"],
    ])
    assert log_deliveries.looks_like(p)
    arrivals, report = log_deliveries.read(p, month="2026-08",
                                           log=lambda *_a: None)
    assert report["marks"] == 2
    assert abs(report["volume_m3"] - 2120.75) < 0.01
    assert arrivals[0]["identifier"] == "GR2106"


def test_arrivals_outside_the_month_are_left_for_it(tmp_path):
    from harp import log_deliveries
    p = _write_csv(tmp_path, "marks.csv", [
        ["Timber Mark", "Date Received", "Net m3"],
        ["GR2106", "2026-08-12", "100"],
        ["GR2107", "2026-09-02", "100"],
    ])
    _arrivals, report = log_deliveries.read(p, month="2026-08",
                                            log=lambda *_a: None)
    assert report["marks"] == 1
    assert report["outside_month"] == 1


def test_a_sectioned_scale_return_reads(tmp_path):
    """A scale return carries the date on its header row and the marks on its
    detail rows, and abbreviates the row type - BOOM_HEADER describes rows
    beginning BH, LOGS describes rows beginning L."""
    from harp import log_deliveries
    p = _write_csv(tmp_path, "scale.csv", [
        ["BOOM_HEADER", "BOOM_NAME", "Arrival_Date", "Volume"],
        ["LOGS", "BOOM_NAME", "TIMBER_MARK", "METRIC_NET"],
        ["BH", "CLRDLS-26-095", "2026-08-21", "495.3"],
        ["L", "CLRDLS-26-095", "GR2108", "83.7"],
        ["L", "CLRDLS-26-095", "A2645", "438.9"],
    ])
    assert log_deliveries.looks_like(p)
    arrivals, report = log_deliveries.read(p, month="2026-08",
                                           log=lambda *_a: None)
    assert report["marks"] == 2
    assert {a["identifier"] for a in arrivals} == {"GR2108", "A2645"}


def test_log_volume_is_not_chip_tonnage(tmp_path):
    """Log cubic metres and chip bone-dry tonnes are different things. The
    same wood arrives twice - as logs, then as chips - and adding both would
    count it twice."""
    from harp import log_deliveries
    p = _write_csv(tmp_path, "marks.csv", [
        ["Timber Mark", "Date Received", "Net m3"],
        ["GR2106", "2026-08-12", "100"],
    ])
    _a, report = log_deliveries.read(p, month="2026-08", log=lambda *_a: None)
    assert "volume_m3" in report
    assert "bdt" not in report


# ────────────────────── the lot walkback ───────────────────────────────────

def test_the_walkback_starts_from_when_a_lot_finished():
    """Not from when it started. A lot can run for weeks and fibre arriving
    partway through went into it; walking back from the start would exclude
    everything delivered during production."""
    import inspect
    from harp import lots
    src = inspect.getsource(lots.walk)
    assert 'd["when"] <= lot.latest' in src
    assert 'd["when"] <= lot.earliest' not in src


def test_the_walkback_sorts_before_it_walks():
    """One delivery record is in date order and reversing it walked backwards
    correctly. A pool of several months is in whatever order the months were
    read, and reversing that walked forwards from the oldest - which
    satisfied every lot from the start of the year."""
    import inspect
    from harp import lots
    src = inspect.getsource(lots.walk)
    assert "reverse=True" in src


def test_the_intake_library_takes_the_latest_submission(tmp_path):
    """A corrected month arrives as -002 with the original left as it was."""
    from harp import lots
    root = tmp_path / "monthly"
    for sub in ("2026-05-001", "2026-05-002"):
        (root / "2026-05" / sub).mkdir(parents=True)
    months = lots.intake_months(str(root))
    assert len(months) == 1
    assert months[0][1].endswith("2026-05-002")


def test_a_log_record_is_not_read_as_a_delivery():
    """Logs arriving to be chipped are the same wood that arrives later as
    chips. Counting both would satisfy a lot twice over."""
    from harp import lots
    assert lots._is_delivery("May 2026-NFP_Load_Delivery_Summary.xlsx")
    assert not lots._is_delivery("Log_Delivery_Record_2026-08.xlsx")
    assert not lots._is_delivery("SOURCE.xlsx")


# ───────────────────────────── species ─────────────────────────────────────

SPECIES_TABLE = {30: ("Douglas-fir", "Pseudotsuga", "menziesii"),
                 35: ("Western hemlock", "Tsuga", "heterophylla"),
                 47: ("", "", "")}


def test_every_return_from_describe_has_the_same_shape():
    """One early return kept the old four-value shape when a fifth was added,
    and species failed for a whole month - every feature fell through to
    neighbour estimation and 79% of the month came back estimated."""
    from harp import species
    for hist in ({}, {47: 100}, {30: 600, 35: 400}):
        got = species.describe(hist, SPECIES_TABLE)
        assert len(got) == 5, "describe({}) returned {}".format(hist,
                                                                len(got))


def test_a_class_with_no_published_name_is_dropped():
    """Not given a placeholder: a placeholder reads as a species and is not
    one."""
    from harp import species
    dominant, readable, _st, _px, nameless = species.describe(
        {30: 600, 35: 300, 47: 100}, SPECIES_TABLE)
    assert dominant == "Douglas-fir"
    assert "class 47" not in readable
    assert 47 in nameless


def test_a_feature_that_is_only_a_nameless_class_has_no_species():
    """So the gaps stage estimates it from neighbours, which is a real answer
    rather than a label for a hole."""
    from harp import species
    dominant, _r, _st, _px, nameless = species.describe({47: 1000},
                                                        SPECIES_TABLE)
    assert dominant == ""
    assert 47 in nameless


def test_a_declared_area_carries_a_dominant_species(tmp_path):
    """The producer states a species per product with its volume. Without a
    dominant these counted as a blank species name in the month's tally,
    which read as a raster class we could not name and was nothing of the
    kind."""
    import json as _json
    from harp.sources import producer_geodata
    doc = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates":
                     [[[-125, 49.5], [-125, 49.51], [-124.99, 49.51],
                       [-125, 49.5]]]},
        "properties": {
            "Originator": "mosaicforests.com", "SourceID": "TEST-1",
            "CountryOfProduction": "CA",
            "Products": [
                {"CommonName": "Douglas-fir", "NetVolume_m3": 100,
                 "ProductionFromDate": "2026-05-02",
                 "ProductionToDate": "2026-05-09"},
                {"CommonName": "Western hemlock", "NetVolume_m3": 400,
                 "ProductionFromDate": "2026-05-02",
                 "ProductionToDate": "2026-05-09"}]}}]}
    p = tmp_path / "Contract-1 Boom-A.json"
    p.write_text(_json.dumps(doc), encoding="utf-8")
    feats, _rep = producer_geodata.read([str(p)], month="2026-05",
                                        log=lambda *_a: None)
    assert feats
    props = feats[0]["properties"]
    # Most volume first - the area is mostly hemlock and should say so.
    assert props["harp_species_dominant"] == "Western hemlock"
    assert props["harp_species"].startswith("Western hemlock")
    assert "producer" in props["harp_species_basis"]


@pytest.mark.parametrize("month,lag,expect_start", [
    ("2026-07", 2, "2026-05-01"),
    ("2026-03", 3, "2025-12-01"),
    ("2026-12", 1, "2026-11-01"),
])
def test_window_across_configurations(month, lag, expect_start):
    start, _end = run_stage._window(month, lag)
    assert start == expect_start
