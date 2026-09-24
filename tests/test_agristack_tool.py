import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from agents.deps import FarmerContext
from agents.tools import agristack

WITH_CONSENT_CALLBACK = {
    "status": "received",
    "callbackSessionId": "S1",
    "body": {
        "status": "success",
        "statusCode": 200,
        "consentName": "CONTINUE WITH CONSENT",
        "data": {
            "farmerData": {"centralId": "30187009346", "frNameEn": "Manohar Singh"},
            "landData": [],
            "sessionId": "S1",
        },
    },
}

WITHOUT_CONSENT_CALLBACK = {
    "status": "received",
    "callbackSessionId": "S2",
    "body": {
        "consentName": "CONTINUE WITHOUT CONSENT",
        "data": {"farmerData": {"centralId": "30187009346"}, "landData": [], "sessionId": "S2"},
    },
}


def _catalog_response(tags=None, error=None):
    """on_search shape produced by the provider backend's AgristackService.buildCatalog."""
    items = []
    if error:
        items.append({"id": "error", "descriptor": {"name": "Error", "short_desc": error}})
    if tags:
        items.append({"id": "30187009346", "tags": tags})
    return {"responses": [{"message": {"catalog": {"providers": [{"id": "agristack-agri", "items": items}]}}}]}


def _raw_tag(code, value):
    return {"descriptor": {"code": code}, "list": [{"descriptor": {"code": "raw"}, "value": json.dumps(value)}]}


def _fake_cache(monkeypatch, store):
    async def get(key, namespace=None):
        return store.get((namespace, key))

    async def set_(key, value, ttl=None, namespace=None):
        store[(namespace, key)] = value
        return True

    monkeypatch.setattr(agristack.cache, "get", get)
    monkeypatch.setattr(agristack.cache, "set", set_)


def _ctx(session_id):
    return SimpleNamespace(deps=FarmerContext(query="weather in my area", session_id=session_id))


# --- session -> farmer link ---


def test_link_with_consent(monkeypatch):
    _fake_cache(monkeypatch, {("callback-status", "S1"): WITH_CONSENT_CALLBACK})
    link = asyncio.run(agristack.get_agristack_link("S1"))
    assert link == {"farmer_id": "30187009346", "has_consent": True}


def test_link_without_consent(monkeypatch):
    _fake_cache(monkeypatch, {("callback-status", "S2"): WITHOUT_CONSENT_CALLBACK})
    link = asyncio.run(agristack.get_agristack_link("S2"))
    assert link == {"farmer_id": "30187009346", "has_consent": False}


def test_link_missing_session(monkeypatch):
    _fake_cache(monkeypatch, {})
    assert asyncio.run(agristack.get_agristack_link("UNKNOWN")) is None
    assert asyncio.run(agristack.get_agristack_link("")) is None


def test_link_redis_down_returns_none(monkeypatch):
    async def boom(*_a, **_k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(agristack.cache, "get", boom)
    assert asyncio.run(agristack.get_agristack_link("S1")) is None


# --- request ---


def _intent_tags(payload):
    return {t["descriptor"]["code"]: t["value"] for t in payload["message"]["intent"]["item"]["tags"]}


def test_category_a_payload_sends_farmer_id_only():
    payload = agristack.build_payload("F1", agristack.CATEGORY_A, session_id="S1")
    intent = payload["message"]["intent"]
    assert intent["provider"]["id"] == "agristack-agri"
    assert intent["item"]["id"] == "agristack-category-a"
    assert _intent_tags(payload) == {"farmerId": "F1"}


def test_category_b_payload_adds_season_and_year(monkeypatch):
    monkeypatch.setenv("AGRISTACK_SEASON", "Kharif")
    monkeypatch.setenv("AGRISTACK_YEAR", "2026")
    payload = agristack.build_payload("F1", agristack.CATEGORY_B, session_id="S1")
    assert payload["message"]["intent"]["item"]["id"] == "agristack-category-b"
    assert _intent_tags(payload) == {"farmerId": "F1", "season": "Kharif", "year": "2026"}


def test_season_and_year(monkeypatch):
    monkeypatch.delenv("AGRISTACK_SEASON", raising=False)
    monkeypatch.delenv("AGRISTACK_YEAR", raising=False)
    assert agristack.current_season_and_year(datetime(2026, 7, 1)) == ("Kharif", "2026")
    assert agristack.current_season_and_year(datetime(2026, 11, 1)) == ("Rabi", "2026")
    assert agristack.current_season_and_year(datetime(2026, 1, 15)) == ("Rabi", "2025")
    assert agristack.current_season_and_year(datetime(2026, 4, 1)) == ("Zaid", "2026")
    monkeypatch.setenv("AGRISTACK_SEASON", "KHARIF")
    monkeypatch.setenv("AGRISTACK_YEAR", "2025-26")
    assert agristack.current_season_and_year(datetime(2026, 1, 15)) == ("KHARIF", "2025-26")


# --- response ---


def test_parse_response_strips_sensitive_fields():
    data = _catalog_response(
        tags=[
            _raw_tag("farmer-data", {"centralId": "F1", "farmerAadhaarHash": "abc", "mobileNumber": "999"}),
            _raw_tag("land-data", [{"villageName": "Raisara", "districtName": "Bemetara", "panchayatName": "P1"}]),
            _raw_tag("crop-survey-data", [{"cropName": "Paddy"}]),
        ]
    )
    sections, error = agristack.parse_agristack_response(data)
    assert error is None
    assert sections["farmer"] == {"centralId": "F1"}
    assert sections["land"] == [{"villageName": "Raisara", "districtName": "Bemetara", "panchayatName": "P1"}]
    assert sections["crop_survey"] == [{"cropName": "Paddy"}]


def test_parse_response_error_item():
    sections, error = agristack.parse_agristack_response(
        _catalog_response(error="farmerId, season and year are required")
    )
    assert sections == {}
    assert error == "farmerId, season and year are required"


# --- tool ---


def test_tools_not_logged_in(monkeypatch):
    _fake_cache(monkeypatch, {})
    assert "not logged in" in asyncio.run(agristack.get_agristack_farmer_location(_ctx("NONE")))
    assert "not logged in" in asyncio.run(agristack.get_agristack_farmer_crops(_ctx("NONE")))


def test_tools_without_consent_do_not_call_agristack(monkeypatch):
    _fake_cache(monkeypatch, {("callback-status", "S2"): WITHOUT_CONSENT_CALLBACK})

    async def must_not_run(*_a, **_k):
        raise AssertionError("AgriStack called without consent")

    monkeypatch.setattr(agristack, "fetch_agristack", must_not_run)
    assert "without consent" in asyncio.run(agristack.get_agristack_farmer_location(_ctx("S2")))
    assert "without consent" in asyncio.run(agristack.get_agristack_farmer_crops(_ctx("S2")))


def _record_fetch(monkeypatch, calls):
    async def fake_fetch(farmer_id, category, session_id="", question_id=""):
        calls.append((farmer_id, category))
        return {"land": [{"villageName": "Raisara", "districtName": "Bemetara"}], "crop_survey": [{"cropName": "Paddy"}]}, None

    monkeypatch.setattr(agristack, "fetch_agristack", fake_fetch)


def test_location_tool_uses_category_a(monkeypatch):
    _fake_cache(monkeypatch, {("callback-status", "S1"): WITH_CONSENT_CALLBACK})
    calls = []
    _record_fetch(monkeypatch, calls)
    out = asyncio.run(agristack.get_agristack_farmer_location(_ctx("S1")))
    assert calls == [("30187009346", "agristack-category-a")]
    assert "Raisara" in out and "forward_geocode" in out


def test_crops_tool_uses_category_b(monkeypatch):
    _fake_cache(monkeypatch, {("callback-status", "S1"): WITH_CONSENT_CALLBACK})
    calls = []
    _record_fetch(monkeypatch, calls)
    out = asyncio.run(agristack.get_agristack_farmer_crops(_ctx("S1")))
    assert calls == [("30187009346", "agristack-category-b")]
    assert "Paddy" in out and "Bemetara" in out


# --- agent context ---


def test_user_message_status_line():
    def message(status):
        return FarmerContext(query="weather in my area", session_id="S1", agristack_status=status).get_user_message()

    assert "AgriStack" not in message("not_logged_in")
    assert "**AgriStack status:** logged in with consent" in message("consent")
    assert "**AgriStack status:** logged in without consent" in message("no_consent")


def test_agristack_tools_hidden_unless_consent():
    tool_def = object()
    for status, expected in (("consent", tool_def), ("no_consent", None), ("not_logged_in", None)):
        ctx = SimpleNamespace(deps=FarmerContext(query="q", session_id="S1", agristack_status=status))
        assert asyncio.run(agristack.only_with_agristack_consent(ctx, tool_def)) is expected


def test_every_language_prompt_includes_agristack_rules():
    from helpers.utils import get_prompt

    for lang in ("en", "hi", "as", "bn", "gu", "kn", "ml", "mr", "ta", "te"):
        prompt = get_prompt(
            f"agrinet_{lang}",
            context={
                "today_date": "x",
                "crop_season": "x",
                "last_weekday_table": "",
                "vector_scheme_count": 0,
                "vector_schemes_bullets": "",
                "vector_schemes_identifiers": "",
            },
        )
        assert "## AgriStack farmer data" in prompt, lang
        assert "consent is not enabled from your end" in prompt, lang
