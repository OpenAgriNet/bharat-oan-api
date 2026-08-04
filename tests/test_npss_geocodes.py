import asyncio
from types import SimpleNamespace

from app.core import npss_geocodes
from app.core.npss_followup import find_pending_npss_image_url
from app.services.chat import _wrap_image_analysis_message
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


def test_resolves_one_farmer_location_by_forward_then_reverse_geocoding(monkeypatch):
    async def fake_forward_geocode(location):
        assert location == "Rewla Khanpur, Delhi"
        return [(28.5648, 76.9822)]

    async def fake_reverse_geocode(latitude, longitude):
        assert (latitude, longitude) == (28.5648, 76.9822)
        return DELHI_PROPS

    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", fake_forward_geocode)
    monkeypatch.setattr(npss_geocodes, "_reverse_geocode_properties", fake_reverse_geocode)
    monkeypatch.setattr(npss_geocodes, "_fetch_master_rows", _fake_master_rows)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            None,
            None,
            bearer_token="token",
            location="Rewla Khanpur, Delhi",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1868",
        "village_id": "65534",
        "latitude": 28.5648,
        "longitude": 76.9822,
    }


def test_browser_coordinates_take_priority_over_farmer_location(monkeypatch):
    calls = []

    async def fake_master_lookup(latitude, longitude, *, bearer_token):
        calls.append((latitude, longitude, bearer_token))
        return {
            "state_id": "1",
            "district_id": "2",
            "sub_district_id": "3",
            "village_id": "4",
        }

    async def unexpected_forward_geocode(location):
        raise AssertionError(location)

    monkeypatch.setattr(npss_geocodes, "_resolve_from_master_apis", fake_master_lookup)
    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", unexpected_forward_geocode)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            19.076,
            72.8777,
            bearer_token="token",
            location="A conflicting typed place",
        )
    )

    assert result == {
        "state_id": "1",
        "district_id": "2",
        "sub_district_id": "3",
        "village_id": "4",
    }
    assert calls == [(19.076, 72.8777, "token")]


def test_farmer_location_falls_back_when_coordinates_are_incomplete(monkeypatch):
    calls = []

    async def fake_master_lookup(latitude, longitude, *, bearer_token):
        del bearer_token
        calls.append((latitude, longitude))
        if len(calls) == 1:
            return {"state_id": "9"}
        return {
            "state_id": "9",
            "district_id": "172",
            "sub_district_id": "1868",
            "village_id": "65534",
        }

    async def fake_forward_geocode(location):
        assert location == "Rewla Khanpur, Delhi"
        return [(28.5648, 76.9822)]

    monkeypatch.setattr(npss_geocodes, "_resolve_from_master_apis", fake_master_lookup)
    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", fake_forward_geocode)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            0.0,
            0.0,
            bearer_token="token",
            location="Rewla Khanpur, Delhi",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1868",
        "village_id": "65534",
        "latitude": 28.5648,
        "longitude": 76.9822,
    }
    assert calls == [(0.0, 0.0), (28.5648, 76.9822)]


def test_typed_location_fills_ids_but_preserves_browser_coordinates(monkeypatch):
    calls = []

    async def fake_master_lookup(latitude, longitude, *, bearer_token):
        del bearer_token
        calls.append((latitude, longitude))
        if len(calls) == 1:
            return {"state_id": "9"}
        return {
            "state_id": "9",
            "district_id": "172",
            "sub_district_id": "1868",
            "village_id": "65534",
        }

    async def fake_forward_geocode(location):
        assert location == "Rewla Khanpur, Delhi"
        return [(28.5648, 76.9822)]

    monkeypatch.setattr(npss_geocodes, "_resolve_from_master_apis", fake_master_lookup)
    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", fake_forward_geocode)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            28.6307647223,
            77.0899123838,
            bearer_token="token",
            location="Rewla Khanpur, Delhi",
        )
    )

    assert result == {
        "state_id": "9",
        "district_id": "172",
        "sub_district_id": "1868",
        "village_id": "65534",
        "latitude": 28.6307647223,
        "longitude": 77.0899123838,
    }
    assert calls == [(28.6307647223, 77.0899123838), (28.5648, 76.9822)]


def test_unresolved_typed_location_does_not_replace_browser_coordinates(monkeypatch):
    calls = []

    async def fake_master_lookup(latitude, longitude, *, bearer_token):
        del bearer_token, longitude
        calls.append(latitude)
        if len(calls) == 1:
            return {"state_id": "9"}
        return {"state_id": "9", "district_id": "163"}

    async def fake_forward_geocode(location):
        assert location == "110058"
        return [(28.6398522, 77.2130306)]

    monkeypatch.setattr(npss_geocodes, "_resolve_from_master_apis", fake_master_lookup)
    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", fake_forward_geocode)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            28.6307647223,
            77.0899123838,
            bearer_token="token",
            location="110058",
        )
    )

    assert result == {"state_id": "9"}
    assert calls == [28.6307647223, 28.6398522]


def test_ambiguous_location_does_not_choose_between_two_hierarchies(monkeypatch):
    async def fake_forward_geocode(location):
        assert location == "Rampur"
        return [(10.0, 70.0), (20.0, 80.0)]

    async def fake_master_lookup(latitude, longitude, *, bearer_token):
        del bearer_token, longitude
        suffix = "1" if latitude == 10.0 else "2"
        return {
            "state_id": suffix,
            "district_id": suffix,
            "sub_district_id": suffix,
            "village_id": suffix,
        }

    monkeypatch.setattr(npss_geocodes, "_forward_geocode_coordinates", fake_forward_geocode)
    monkeypatch.setattr(npss_geocodes, "_resolve_from_master_apis", fake_master_lookup)

    result = asyncio.run(
        npss_geocodes.resolve_npss_location_ids(
            None,
            None,
            bearer_token="token",
            location="Rampur",
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


def test_failed_npss_attempt_does_not_leave_location_conversation_looping():
    history = [
        SimpleNamespace(
            parts=[
                SimpleNamespace(
                    part_kind="tool-return",
                    tool_name="analyze_crop_image",
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
                    tool_name="analyze_crop_image",
                    content="The pest analysis service encountered an unexpected error.",
                )
            ]
        ),
    ]

    assert find_pending_npss_image_url(history) is None


def test_image_analysis_uses_kvk_when_coordinates_are_missing(monkeypatch):
    captured = {}

    async def fake_token():
        return "token"

    async def unexpected_geocode(*args, **kwargs):
        raise AssertionError("geocoding must not run without browser coordinates")

    async def fake_download(image_url):
        return b"\xff\xd8\xffimage", "image/jpeg"

    async def fake_analyze(**kwargs):
        captured.update(kwargs)
        return {"pest": "Test pest", "crop": "Cotton"}

    monkeypatch.setattr(npss, "_get_cached_npss_token", fake_token)
    monkeypatch.setattr(npss, "resolve_npss_location_ids", unexpected_geocode)
    monkeypatch.setattr(npss, "_download_image", fake_download)
    monkeypatch.setattr(npss, "_call_npss_analyze", fake_analyze)
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

    assert captured["state_id"] == "9"
    assert captured["district_id"] == "172"
    assert captured["sub_district_id"] == "1869"
    assert captured["village_id"] == "65600"
    assert captured["latitude"] == 28.569352
    assert captured["longitude"] == 76.895681
    assert "[NPSS_LOCATION_REQUIRED]" not in result
    assert "**NPSS Analysis Result**" in result


def test_image_analysis_ignores_typed_location_without_coordinates(monkeypatch):
    captured = {}

    async def fake_token():
        return "token"

    async def unexpected_geocode(*args, **kwargs):
        raise AssertionError("typed locations must not trigger geocoding")

    async def fake_download(image_url):
        assert image_url == "https://example.test/cotton.jpg"
        return b"\xff\xd8\xffimage", "image/jpeg"

    async def fake_analyze(**kwargs):
        captured.update(kwargs)
        return {"pest": "Test pest", "crop": "Cotton"}

    monkeypatch.setattr(npss, "_get_cached_npss_token", fake_token)
    monkeypatch.setattr(npss, "resolve_npss_location_ids", unexpected_geocode)
    monkeypatch.setattr(npss, "_download_image", fake_download)
    monkeypatch.setattr(npss, "_call_npss_analyze", fake_analyze)
    deps = FarmerContext(query="Analyze", lang_code="hi", session_id="session")
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        npss.analyze_crop_image(
            ctx,
            "https://example.test/cotton.jpg",
            location="Rewla Khanpur, Delhi",
        )
    )

    assert captured["state_id"] == "9"
    assert captured["district_id"] == "172"
    assert captured["sub_district_id"] == "1869"
    assert captured["village_id"] == "65600"
    assert captured["latitude"] == 28.569352
    assert captured["longitude"] == 76.895681
    assert "**NPSS Analysis Result**" in result


def test_image_analysis_falls_back_to_kvk_when_coordinates_do_not_resolve(monkeypatch):
    captured = {}

    async def fake_token():
        return "token"

    async def unresolved_coordinates(*args, **kwargs):
        assert args == (12.34, 56.78)
        assert kwargs == {"bearer_token": "token"}
        return None

    async def fake_download(image_url):
        return b"\xff\xd8\xffimage", "image/jpeg"

    async def fake_analyze(**kwargs):
        captured.update(kwargs)
        return {"pest": "Test pest", "crop": "Cotton"}

    monkeypatch.setattr(npss, "_get_cached_npss_token", fake_token)
    monkeypatch.setattr(npss, "resolve_npss_location_ids", unresolved_coordinates)
    monkeypatch.setattr(npss, "_download_image", fake_download)
    monkeypatch.setattr(npss, "_call_npss_analyze", fake_analyze)
    deps = FarmerContext(
        query="Analyze",
        lang_code="hi",
        session_id="session",
        latitude=12.34,
        longitude=56.78,
    )

    result = asyncio.run(
        npss.analyze_crop_image(
            SimpleNamespace(deps=deps),
            "https://example.test/cotton.jpg",
        )
    )

    assert captured["state_id"] == "9"
    assert captured["district_id"] == "172"
    assert captured["sub_district_id"] == "1869"
    assert captured["village_id"] == "65600"
    assert captured["latitude"] == 28.569352
    assert captured["longitude"] == 76.895681
    assert "**NPSS Analysis Result**" in result


def test_image_analysis_uses_zero_village_for_verified_urban_hierarchy(monkeypatch):
    captured = {}

    async def fake_token():
        return "token"

    async def resolved_without_village(*args, **kwargs):
        assert args == (28.6307647223, 77.0899123838)
        assert kwargs == {"bearer_token": "token"}
        return {
            "state_id": "9",
            "district_id": "173",
            "sub_district_id": "7323",
        }

    async def fake_download(image_url):
        assert image_url == "https://example.test/cotton.jpg"
        return b"\xff\xd8\xffimage", "image/jpeg"

    async def fake_analyze(**kwargs):
        captured.update(kwargs)
        return {"pest": "Test pest", "crop": "Cotton"}

    monkeypatch.setattr(npss, "_get_cached_npss_token", fake_token)
    monkeypatch.setattr(npss, "resolve_npss_location_ids", resolved_without_village)
    monkeypatch.setattr(npss, "_download_image", fake_download)
    monkeypatch.setattr(npss, "_call_npss_analyze", fake_analyze)
    deps = FarmerContext(
        query="Analyze",
        lang_code="hi",
        session_id="session",
        latitude=28.6307647223,
        longitude=77.0899123838,
    )
    ctx = SimpleNamespace(deps=deps)

    result = asyncio.run(
        npss.analyze_crop_image(
            ctx,
            "https://example.test/cotton.jpg",
        )
    )

    assert captured["state_id"] == "9"
    assert captured["district_id"] == "173"
    assert captured["sub_district_id"] == "7323"
    assert captured["village_id"] == "0"
    assert captured["latitude"] == 28.6307647223
    assert captured["longitude"] == 77.0899123838
    assert "**NPSS Analysis Result**" in result


def test_pending_image_instruction_retries_without_location():
    wrapped = _wrap_image_analysis_message(
        "Rewla Khanpur, Delhi",
        None,
        None,
        pending_npss_image_url="http://localhost:8000/api/image/pending",
    )

    assert "Call `analyze_crop_image` again" in wrapped
    assert "with that image URL and no location" in wrapped
    assert "Do not ask the farmer for location details" in wrapped
    assert "Krishi Vigyan Kendra, Delhi" in wrapped


def test_image_instruction_uses_kvk_without_coordinates():
    wrapped = _wrap_image_analysis_message("Analyze", None, None)

    assert "Call `analyze_crop_image` immediately without a location" in wrapped
    assert "Do not ask the farmer for location details" in wrapped
    assert "Krishi Vigyan Kendra, Delhi" in wrapped
