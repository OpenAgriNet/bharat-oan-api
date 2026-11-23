import asyncio
import json
import logging
from types import SimpleNamespace

import httpx

import pytest

from agents.tools import sathi_seed as v1
from agents.tools.v2 import sathi_seed

OPERATION_REF = (
    "https://raw.githubusercontent.com/OpenAgriNet/network-specs/bv-features/api/external/sathi/stock/v0.2/openapi.yaml"
    "#/paths/~1inv-apis~1stock~1getSeedAvailability/post"
)
SATHI_STOCK_FILTER = '$.catalogs[*].resources[*] ? (@.descriptor.code == "sathi-stock")'
LAT, LON = 20.0, 73.8
MAHARASHTRA_ONLY = (
    "SATHI seed information is currently available only for Maharashtra. "
    "Would you like to check a district in Maharashtra instead?"
)


def _resource(district_code="487", district_name="Nashik"):
    return {
        "id": f"res-sathi-stock:27:{district_code}",
        "descriptor": {"code": "sathi-stock", "name": f"SATHI seed stock - {district_name}"},
        "resourceAttributes": {
            "@context": sathi_seed.AGRICULTURE_RESOURCE_CONTEXT,
            "@type": "openagrinet:AgricultureResource",
            "informationMode": "OnDemand",
            "subjectCategories": ["Crop"],
            "agricultureSubjects": [
                {"descriptor": {"code": "C001", "name": "Paddy"}},
                {"descriptor": {"code": "C002", "name": "Wheat"}},
            ],
            "coverageAreas": [
                {"codeScheme": "LGD", "areaLevel": "State", "areaCode": "27", "areaName": "Maharashtra"},
                {"codeScheme": "LGD", "areaLevel": "District", "areaCode": district_code, "areaName": district_name},
                {"type": "Polygon", "coordinates": [[[73.0, 19.0], [74.0, 19.0], [74.0, 21.0], [73.0, 19.0]]]},
            ],
            "actions": [{"action": "select", "operationRef": OPERATION_REF}],
        },
    }


PROVIDER = {"id": "sathi", "descriptor": {"code": "sathi", "name": "SATHI"}}


def _on_discover(*resources):
    return {"message": {"catalogs": [{"provider": PROVIDER, "resources": list(resources)}]}}


def _dealer(dealer_id, name, bags, quintals=None, contact="9000000000"):
    return {
        "dealer_id": dealer_id,
        "dealer_name": name,
        "district": "Nashik",
        "state": "Maharashtra",
        "contact_number": contact,
        "bags": bags,
        "quintals": quintals if quintals is not None else bags * 0.3,
    }


def _lot(variety, dealers):
    return {
        "lot_number": "L1",
        "variety_id": "V1",
        "variety_name": variety,
        "crop_id": "C001",
        "crop_name": "Paddy",
        "seed_class": "CERTIFIED I",
        "bag_weight_kg": 30,
        "total_bags": sum(d["bags"] for d in dealers),
        "total_quintals": 0,
        "available_at": dealers,
    }


def _on_select(status_code=200, message="Success", data=None):
    answer = {
        "@context": sathi_seed.AGRICULTURE_RESOURCE_CONTEXT,
        "@type": "openagrinet:AgricultureResource",
        "statusCode": status_code, "status": "x", "message": message, "data": data or [],
    }
    return {"message": {"contract": {"commitments": [{"resources": [{"resourceAttributes": answer}]}]}}}


class FakeAdapter:
    def __init__(self, discover=None, select=None):
        self.discover = discover if discover is not None else _on_discover(_resource())
        self.select = select if select is not None else _on_select()
        self.bodies = {}

    def __call__(self, request):
        action = request.url.path.rsplit("/", 1)[-1]
        self.bodies[action] = json.loads(request.content)
        answer = getattr(self, action)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("OAN_ADAPTER_URL", "http://adapter.test/consumer")
    monkeypatch.setenv("OAN_NETWORK_ID", "oan.test")
    monkeypatch.setenv("OAN_DOMAIN", "agriculture")
    monkeypatch.delenv("OAN_PUBLIC_NETWORK_ID", raising=False)
    monkeypatch.setattr(sathi_seed, "lf_update_current_observation", lambda **kwargs: None)
    fake = FakeAdapter()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        sathi_seed.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs),
    )
    return fake


def _search(crop_code="C001"):
    return asyncio.run(sathi_seed.search_sathi_seed_availability(SimpleNamespace(), crop_code, LAT, LON))


def _list():
    return asyncio.run(sathi_seed.list_sathi_crops(SimpleNamespace(), LAT, LON))


def _v1_render(lots, location):
    acc = {}
    for lot in lots:
        for dealer in lot["available_at"]:
            v1._accumulate_dealer(acc, {"value": dealer}, lot["variety_name"])
    return v1._format_dealer_details(acc, location, include_source_footer=True)


def test_discover_asks_the_public_catalog_with_lon_lat_point(adapter):
    _search()
    body = adapter.bodies["discover"]
    assert body["context"]["networkId"] == "*"
    assert body["context"]["action"] == "discover"
    assert body["context"]["senderId"] == "oan.test"
    assert body["context"]["schemaContext"] == [
        sathi_seed.AGRICULTURE_RESOURCE_CONTEXT + "#openagrinet:AgricultureResource"
    ]
    spatial = body["message"]["intent"]["spatial"][0]
    assert spatial["op"] == "S_INTERSECTS"
    assert spatial["geometry"] == {"type": "Point", "coordinates": [LON, LAT]}
    assert body["message"]["intent"]["filters"]["expression"] == SATHI_STOCK_FILTER


def test_select_carries_resource_type_provider_descriptor_and_four_codes(adapter):
    _search("c001")
    body = adapter.bodies["select"]
    assert body["context"]["networkId"] == "oan.test"
    assert body["context"]["bppUri"] == "https://oan.test"
    assert body["context"]["transactionId"] == adapter.bodies["discover"]["context"]["transactionId"]
    commitment = body["message"]["contract"]["commitments"][0]
    assert commitment["resources"][0]["resourceAttributes"] == {
        "@context": sathi_seed.AGRICULTURE_RESOURCE_CONTEXT,
        "@type": "openagrinet:AgricultureResource",
        "cropCode": "C001", "seedClass": "CERTIFIED I", "stateCode": "27", "districtCode": "487",
    }
    assert commitment["offer"]["provider"] == PROVIDER
    assert commitment["offer"]["resourceIds"] == ["res-sathi-stock:27:487"]
    assert "commitmentAttributes" not in commitment


def test_select_never_sends_an_api_key(adapter):
    _search()
    assert "apikey" not in json.dumps(adapter.bodies["select"]).lower()


def test_rendering_matches_v1_dealer_details(adapter):
    lots = [
        _lot("Indrayani", [_dealer("D1", "Krishi Kendra", 10), _dealer("D2", "Shetkari Seeds", 4)]),
        _lot("Ratnagiri 1", [_dealer("D1", "Krishi Kendra", 3)]),
    ]
    adapter.select = _on_select(data=lots)
    assert _search() == _v1_render(lots, "Nashik, Maharashtra")


def test_zero_bag_dealer_rows_are_dropped(adapter):
    adapter.select = _on_select(data=[_lot("Indrayani", [_dealer("D1", "Krishi Kendra", 10), _dealer("D9", "Empty Shop", 0)])])
    result = _search()
    assert "Krishi Kendra" in result
    assert "Empty Shop" not in result


def test_empty_dealer_rows_are_logged_once_per_search_with_totals(adapter, caplog):
    adapter.select = _on_select(data=[
        _lot("Indrayani", [_dealer("D1", "Krishi Kendra", 10), _dealer("D8", "Empty One", 0)]),
        _lot("Tilak", [_dealer("D9", "Empty Two", 0), _dealer("D7", "Empty Three", 0)]),
        _lot("Samrudhi", [_dealer("D1", "Krishi Kendra", 5)]),
    ])
    with caplog.at_level(logging.INFO, logger="agents.tools.v2.sathi_seed"):
        _search()
    skipped = [r for r in caplog.records if r.levelno == logging.INFO and "no bags in stock" in r.getMessage()]
    assert len(skipped) == 1
    assert "skipped 3 dealer rows" in skipped[0].getMessage()
    assert "across 2 of 3 lots" in skipped[0].getMessage()


def test_no_stock_status_returns_v1_data_not_available_text(adapter):
    adapter.select = _on_select(status_code=409, message="Data Not Found")
    assert _search() == v1._seed_availability_data_not_available_response("Nashik, Maharashtra")


def test_all_zero_bag_lots_return_v1_no_dealers_text(adapter):
    adapter.select = _on_select(data=[_lot("Indrayani", [_dealer("D1", "Krishi Kendra", 0)])])
    assert _search("c001") == (
        "No dealers with stock found for this crop in Nashik, Maharashtra, crop C001.\n\n"
        "**Source: SATHI**"
    )


def test_point_outside_any_district_returns_maharashtra_sentence(adapter):
    adapter.discover = _on_discover()
    assert _search() == MAHARASHTRA_ONLY
    assert "select" not in adapter.bodies


def test_point_in_two_districts_lists_both_and_does_not_select(adapter):
    adapter.discover = _on_discover(_resource("487", "Nashik"), _resource("490", "Pune"))
    result = _search()
    assert "Nashik" in result and "Pune" in result
    assert "select" not in adapter.bodies


def test_crop_code_not_in_district_list_is_refused_without_select(adapter):
    result = _search("Z999")
    assert "Z999" in result
    assert "select" not in adapter.bodies


def test_adapter_error_returns_service_error_text(adapter):
    adapter.select = httpx.Response(500, text="boom")
    assert _search() == "Seed availability service returned an error. Please try again later."


def test_timeout_returns_timeout_text_without_url(adapter):
    adapter.select = httpx.ReadTimeout("slow")
    result = _search()
    assert result == "Seed availability request timed out. Please try again later."


def test_unreachable_adapter_returns_could_not_reach_text(adapter):
    adapter.discover = httpx.ConnectError("refused")
    assert _search() == "Could not reach seed availability service. Please try again later."


def test_list_returns_district_state_and_crop_lines(adapter):
    result = _list()
    assert "Nashik" in result and "Maharashtra" in result
    assert "C001: Paddy" in result
    assert "C002: Wheat" in result
    assert "select" not in adapter.bodies


def test_list_outside_any_district_returns_maharashtra_sentence(adapter):
    adapter.discover = _on_discover()
    assert _list() == MAHARASHTRA_ONLY


def test_list_in_two_districts_names_both(adapter):
    adapter.discover = _on_discover(_resource("487", "Nashik"), _resource("490", "Pune"))
    result = _list()
    assert "Nashik" in result and "Pune" in result
    assert "C001" not in result


def _two_districts():
    return _on_discover(_resource("466", "Ahilyanagar"), _resource("490", "Pune"))


def _search_in(district, crop_code="C001"):
    return asyncio.run(sathi_seed.search_sathi_seed_availability(SimpleNamespace(), crop_code, LAT, LON, district))


def test_named_district_picks_that_district_among_two(adapter):
    adapter.discover = _two_districts()
    _search_in("Pune")
    payload = adapter.bodies["select"]["message"]["contract"]["commitments"][0]["resources"][0]["resourceAttributes"]
    assert payload["districtCode"] == "490"


def test_named_district_matches_ignoring_case_and_whitespace(adapter):
    adapter.discover = _two_districts()
    _search_in("  pune ")
    payload = adapter.bodies["select"]["message"]["contract"]["commitments"][0]["resources"][0]["resourceAttributes"]
    assert payload["districtCode"] == "490"


def test_unmatched_district_name_lists_candidates_without_select(adapter):
    adapter.discover = _two_districts()
    result = _search_in("Nashik")
    assert "Ahilyanagar" in result and "Pune" in result
    assert "district" in result and "same latitude" in result
    assert "select" not in adapter.bodies
    assert len([a for a in adapter.bodies if a == "discover"]) == 1


def test_multi_district_reply_asks_to_call_again_with_district(adapter):
    adapter.discover = _two_districts()
    result = _search()
    assert "same latitude" in result
    assert "location inside that district" not in result


def test_single_discovered_district_wins_over_a_different_name(adapter):
    _search_in("Pune")
    payload = adapter.bodies["select"]["message"]["contract"]["commitments"][0]["resources"][0]["resourceAttributes"]
    assert payload["districtCode"] == "487"


def test_list_with_district_returns_that_districts_crops(adapter):
    adapter.discover = _two_districts()
    result = asyncio.run(sathi_seed.list_sathi_crops(SimpleNamespace(), LAT, LON, "Pune"))
    assert "district Pune" in result
    assert "Ahilyanagar" not in result
    assert "C001: Paddy" in result
