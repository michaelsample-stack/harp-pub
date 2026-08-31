"""Offline tests. Nothing here touches the network."""

from harp.sources import ften
from harp import normalise, router


def test_where_uses_client_number_not_name():
    w = ften.build_where(client_numbers=["00158809"])
    assert "CLIENT_NUMBER IN ('00158809')" in w
    assert "CLIENT_NAME" not in w


def test_where_pairs_number_with_location():
    w = ften.build_where(client_locations={"00002176": ["03", "42"]})
    assert "CLIENT_NUMBER = '00002176'" in w
    assert "CLIENT_LOCATION_CODE IN ('03','42')" in w


def test_completion_rule_defaults_require_both_dates():
    sql = ften.CompletionRule(start_after="2025-07-01").sql()
    assert "DISTURBANCE_END_DATE IS NOT NULL" in sql
    assert "DISTURBANCE_START_DATE IS NOT NULL" in sql
    assert "DISTURBANCE_START_DATE > DATE '2025-07-01'" in sql


def test_completion_rule_can_relax_start():
    sql = ften.CompletionRule(require_start=False).sql()
    assert not any("START_DATE IS NOT NULL" in s for s in sql)


def test_quotes_are_escaped():
    w = ften.build_where(districts=["O'Hara"])
    assert "O''Hara" in w


def test_empty_where_is_valid_sql():
    assert ften.build_where() == "1=1"


def test_size_class_matches_eudr_article_9():
    assert normalise.size_class(0.5) == "discard"
    assert normalise.size_class(2.0) == "point"
    assert normalise.size_class(10.0) == "polygon"


def test_normalised_row_carries_provenance():
    feature = {
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
        "properties": {"TIMBER_MARK": "A83888", "CUT_BLOCK_SKEY": 12345,
                       "CUT_BLOCK_ID": "7A", "GEOGRAPHIC_DISTRICT_NAME": "South Island"},
    }
    row = normalise.from_ften(feature, source="NFP")
    assert row["eudr_sub_type"] == "database_polygon"
    assert row["sce_id"] == "FTEN-A83888-12345"
    assert row["_source"]["TIMBER_MARK"] == "A83888"
    assert normalise.check(row) == []


def test_missing_geometry_is_caught():
    row = normalise.from_ften({"properties": {"CUT_BLOCK_SKEY": 1}}, source="x")
    assert "missing geometry" in normalise.check(row)


def test_router_prefers_supplied_geodata_over_ften():
    s = router.Supplier(supplier_id="S1", name="Test", jurisdiction="BC",
                        land_type="public", client_number="00158809",
                        geodata_format="geojson")
    assert router.choose(s) is router.Path.SUPPLIER_GEODATA


def test_router_falls_through_to_unresolved():
    s = router.Supplier(supplier_id="S2", name="Test", jurisdiction="WA",
                        land_type="private")
    assert router.choose(s) is router.Path.UNRESOLVED


def test_router_bc_public_uses_ften():
    s = router.Supplier(supplier_id="S3", name="Test", jurisdiction="BC",
                        land_type="public", client_number="00158809")
    assert router.choose(s) is router.Path.FTEN_PUBLIC
