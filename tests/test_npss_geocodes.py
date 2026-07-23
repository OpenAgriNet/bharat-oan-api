import asyncio
from types import SimpleNamespace

from app.core import npss_geocodes
from app.core.npss_followup import build_npss_location_request, find_pending_npss_image_url
from agents.deps import FarmerContext
from agents.tools import npss


DELHI_PROPS = {
    "osm_key": "place",
    "osm_value": "locality",
    "type": "locality",
    "name": "Rewla Khanpur",
    "city": "Delhi",
    "district": "Kapashera Tehsil",
    "_nearby": [
        {
            "osm_key": "boundary",
            "osm_value": "political",
            "type": "other",
            "name": "Rewla Khanpur",
            "city": "Delhi",
            "district": "South West Delhi",
        },
        {
            "osm_key": "boundary",
            "osm_value": "administrative",
            "type": "district",
            "name": "Kapashera Tehsil",
            "city": "Delhi",
        },
        {
            "osm_key": "amenity",
            "type": "house",
            "name": "DGD Kanganheri",
            "state": "Delhi",
            "district": "Kapashera Tehsil",
        },
    ],
}


async def _fake_master_rows(endpoint, *, bearer_token, params=None):
    del bearer_token
    rows = {
        "States": [{"stateId": "9", "stateName": "Delhi"}],
        "Districts": [{"districtId": "172", "districtName": "South West"}],
        "SubDistricts": [{"subDistrictId": "1868", "subDistrictName": "Kapeshera"}],
        "Vilages": [{"villageId": "65534", "villageName": "Rewla Kham Pur"}],
    }
    assert endpoint in rows
    if endpoint == "Districts":
        assert params == {"stateId": "9"}
    elif endpoint == "SubDistricts":
        assert params == {"stateId": "9", "districtId": "172"}
    elif endpoint == "Vilages":
        assert params == {"stateId": "9", "districtId": "172", "subDistrictId": "1868"}
    return rows[endpoint]


def test_resolves_delhi_when_photon_admin_fields_are_split_across_nearby_results(monkeypatch):
    async def fake_reverse_geocode(latitude, longitude):
        assert (latitude, longitude) == (28.5648, 76.9822)
        return DELHI_PROPS

    monkeypatch.setattr(npss_geocodes, "_reverse_geocode_properties", fake_reverse_geocode)
    monkeypatch.setattr(npss_geocodes, "_fetch_master_rows", _fake_master_rows)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            28.5648,
            76.9822,
            bearer_token="token",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1868",
        "village_id": "65534",
    }


def test_resolves_farmer_provided_location_names_against_master_hierarchy(monkeypatch):
    monkeypatch.setattr(npss_geocodes, "_fetch_master_rows", _fake_master_rows)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            None,
            None,
            bearer_token="token",
            state="Delhi",
            district="South West Delhi District",
            sub_district="Kapashera Tehsil",
            village="Rewla Khanpur",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1868",
        "village_id": "65534",
    }


def test_verified_matching_handles_parent_suffix_and_unique_spelling_drift():
    district = npss_geocodes._pick_verified_row(
        [{"districtId": "172", "districtName": "South West"}],
        ["South West Delhi District"],
        ("districtName", "name"),
        parent_names=["Delhi"],
    )
    subdistrict = npss_geocodes._pick_verified_row(
        [
            {"subDistrictId": "1868", "subDistrictName": "Kapeshera"},
            {"subDistrictId": "1869", "subDistrictName": "Najafgarh"},
        ],
        ["Kapashera Tehsil"],
        ("subDistrictName", "name"),
        similarity_threshold=0.84,
    )

    assert district == {"districtId": "172", "districtName": "South West"}
    assert subdistrict == {"subDistrictId": "1868", "subDistrictName": "Kapeshera"}


def test_verified_matching_rejects_a_close_ambiguous_location():
    match = npss_geocodes._pick_verified_row(
        [
            {"villageId": "1", "villageName": "Rampur Kalan"},
            {"villageId": "2", "villageName": "Rampur Khurd"},
        ],
        ["Rampur"],
        ("villageName", "name"),
        similarity_threshold=0.5,
    )

    assert match is None


def test_duplicate_subdistrict_names_are_disambiguated_by_village(monkeypatch):
    async def duplicate_master_rows(endpoint, *, bearer_token, params=None):
        del bearer_token
        if endpoint == "States":
            return [{"stateId": "9", "stateName": "Delhi"}]
        if endpoint == "Districts":
            return [{"districtId": "172", "districtName": "South West"}]
        if endpoint == "SubDistricts":
            return [
                {"subDistrictId": "1867", "subDistrictName": "Dwarka"},
                {"subDistrictId": "7321", "subDistrictName": "Dwarka"},
            ]
        if endpoint == "Vilages" and params["subDistrictId"] == "1867":
            return [{"villageId": "10", "villageName": "Amber Hai"}]
        if endpoint == "Vilages" and params["subDistrictId"] == "7321":
            return [{"villageId": "11", "villageName": "Bamnoli"}]
        raise AssertionError((endpoint, params))

    monkeypatch.setattr(npss_geocodes, "_fetch_master_rows", duplicate_master_rows)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            None,
            None,
            bearer_token="token",
            state="Delhi",
            district="South West Delhi",
            sub_district="Dwarka",
            village="Amberhai",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1867",
        "village_id": "10",
    }


def test_wrong_village_is_not_forced_to_nearest_master_match(monkeypatch):
    monkeypatch.setattr(npss_geocodes, "_fetch_master_rows", _fake_master_rows)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            None,
            None,
            bearer_token="token",
            state="Delhi",
            district="South West Delhi",
            sub_district="Kapashera",
            village="Completely Different Village",
        )
    )

    assert result is None


def test_finds_pending_npss_image_url_from_tool_history():
    image_url = "http://localhost:8000/api/image/9964ff75-cfe1-42bc-a6b7-55249920a38a"
    history = [
        SimpleNamespace(
            parts=[
                SimpleNamespace(
                    part_kind="tool-return",
                    content=(
                        "[NPSS_LOCATION_REQUIRED]\n"
                        f"[IMAGE_URL: {image_url}]\n"
                        "Please ask for the location."
                    ),
                )
            ]
        )
    ]

    assert find_pending_npss_image_url(history) == image_url


def test_completed_npss_result_clears_older_pending_image():
    history = [
        SimpleNamespace(
            parts=[
                SimpleNamespace(
                    part_kind="tool-return",
                    content=(
                        "[NPSS_LOCATION_REQUIRED]\n"
                        "[IMAGE_URL: http://localhost:8000/api/image/9964ff75-cfe1-42bc-a6b7-55249920a38a]"
                    ),
                )
            ]
        ),
        SimpleNamespace(
            parts=[
                SimpleNamespace(
                    part_kind="tool-return",
                    content="**NPSS Analysis Result**\n\n**pest:** Example",
                )
            ]
        ),
    ]

    assert find_pending_npss_image_url(history) is None


def test_image_analysis_requests_location_without_reporting_failure(monkeypatch):
    async def fake_token():
        return "token"

    async def unresolved_location(*args, **kwargs):
        return None

    monkeypatch.setattr(npss, "_get_cached_npss_token", fake_token)
    monkeypatch.setattr(npss, "resolve_npss_location_ids", unresolved_location)
    deps = FarmerContext(
        query="Analyze this image",
        lang_code="hi",
        session_id="session",
        latitude=None,
        longitude=None,
    )
    ctx = SimpleNamespace(deps=deps)
    image_url = "http://localhost:8000/api/image/9964ff75-cfe1-42bc-a6b7-55249920a38a"

    result = asyncio.run(npss.analyze_crop_image(ctx, image_url))

    assert "[NPSS_LOCATION_REQUIRED]" in result
    assert f"[IMAGE_URL: {image_url}]" in result
    assert "state, district, sub-district/tehsil, village" in result
    assert "failed" not in result.lower()
    assert deps.npss_location_required is True
    assert deps.npss_missing_location_fields == [
        "state",
        "district",
        "sub-district/tehsil",
        "village",
    ]


def test_hindi_location_request_starts_a_conversation_without_failure_language():
    result = build_npss_location_request(
        "hi",
        ["state", "district", "sub-district/tehsil", "village"],
    )

    assert "राज्य" in result
    assert "जिला" in result
    assert "तहसील" in result
    assert "गांव" in result
    assert "छवि सुरक्षित है" in result
    assert "नहीं किया जा सका" not in result
