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
        "States": [{"stateId": "7", "stateName": "Delhi"}],
        "Districts": [{"districtId": "97", "districtName": "South West Delhi"}],
        "SubDistricts": [{"subDistrictId": "453", "subDistrictName": "Kapashera"}],
        "Vilages": [{"villageId": "64099", "villageName": "Rewla Khanpur"}],
    }
    assert endpoint in rows
    if endpoint == "Districts":
        assert params == {"stateId": "7"}
    elif endpoint == "SubDistricts":
        assert params == {"stateId": "7", "districtId": "97"}
    elif endpoint == "Vilages":
        assert params == {"stateId": "7", "districtId": "97", "subDistrictId": "453"}
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
        "state_id": "7",
        "district_id": "97",
        "sub_district_id": "453",
        "village_id": "64099",
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
        "state_id": "7",
        "district_id": "97",
        "sub_district_id": "453",
        "village_id": "64099",
    }


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
