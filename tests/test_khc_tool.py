import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.tools import khc


def ctx(session_id="session-1"):
    return SimpleNamespace(deps=SimpleNamespace(session_id=session_id, question_id="q1"))


def registration(**changes):
    return {
        "mobile_number": "9876543210", "farmer_type": "existing",
        "grievance_text": "Bank refused loan", "farmer_language": "hi",
        "state_id": 10, "village_id": 219159, "scheme_id": 38, "division_id": 31,
        "problem_category_id": 39, "problem_subcategory_id": 1725, "confirmed": True,
        **changes,
    }


def envelope(action="status", code=None, fields=None, message="Retrieved"):
    tag = {
        "descriptor": {
            "code": code or ("grievance_status" if action == "status" else "grievance_created"),
            "short_desc": message,
        },
        "list": [{"descriptor": {"code": key}, "value": value} for key, value in (
            fields if fields is not None else {
                "grievanceNo": "BR/240926/2", "currentStatus": "OPEN",
                "currentLevel": "STATE", "createdAt": "2026-09-24",
            }
        ).items()],
    }
    order = {"tags": [tag]} if action == "status" else {"items": [{"tags": [tag]}]}
    return {"message": {"order": order}}


def fake_http(monkeypatch, response, calls):
    monkeypatch.setenv("BAP_ENDPOINT", "https://bap.test")
    real_client = khc.httpx.AsyncClient

    def respond(request):
        import json
        calls.append(json.loads(request.content))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        khc.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=khc.httpx.MockTransport(respond), **kwargs),
    )


@pytest.mark.parametrize("number", ["", "abc\r\nbad", "a" * 101])
def test_status_asks_for_number_without_http(monkeypatch, number):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert "Ask the farmer" in asyncio.run(khc.check_khc_grievance_status(ctx(), number))
    call.assert_not_called()


def test_status_preserves_slashes_and_normalizes_regional_digits(monkeypatch):
    call = AsyncMock(return_value="OPEN")
    monkeypatch.setattr(khc, "_call", call)
    assert asyncio.run(khc.check_khc_grievance_status(ctx(), " BR/२४०९२६/२ ")) == "OPEN"
    assert call.call_args.kwargs == {"grievanceNo": "BR/240926/2"}


@pytest.mark.parametrize("changes,expected", [
    ({"mobile_number": ""}, "mobile"),
    ({"farmer_type": ""}, "OTP first"),
    ({"grievance_text": ""}, "describe"),
    ({"grievance_text": "a" * 5001}, "describe"),
    ({"farmer_language": ""}, "language"),
    ({"farmer_type": "new"}, "name and gender"),
    ({"state_id": None}, "mappings"),
    ({"village_id": True}, "mappings"),
    ({"scheme_id": 0}, "mappings"),
    ({"problem_category_id": 9007199254740992}, "mappings"),
    ({"confirmed": False}, "confirmation"),
])
def test_registration_collects_validated_details_and_confirmation(monkeypatch, changes, expected):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert expected in asyncio.run(khc.register_khc_grievance(ctx(), **registration(**changes)))
    call.assert_not_called()


@pytest.mark.parametrize("farmer_type", ["new", "existing"])
def test_registration_uses_verified_ids_and_stable_reference(monkeypatch, farmer_type):
    call = AsyncMock(return_value="Registered")
    monkeypatch.setattr(khc, "_call", call)
    values = registration(farmer_type=farmer_type, farmer_name="Test Farmer", gender="M")
    assert asyncio.run(khc.register_khc_grievance(ctx(), **values)) == "Registered"
    sent = call.call_args.kwargs
    assert sent["farmerMobileNumber"] == "9876543210"
    assert sent["confirmed"] == "true"
    assert sent["stateId"] == "10"
    assert sent["farmerType"] == farmer_type
    assert ("farmerName" in sent) == (farmer_type == "new")
    first_reference = sent["externalReferenceNo"]
    asyncio.run(khc.register_khc_grievance(ctx(), **values))
    assert call.call_args.kwargs["externalReferenceNo"] == first_reference


def test_registration_requires_session(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert "active conversation" in asyncio.run(khc.register_khc_grievance(ctx(""), **registration()))
    call.assert_not_called()


@pytest.mark.parametrize("action", ["status", "init"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_network_envelope_and_rendering(monkeypatch, action, wrapped):
    result = envelope(action)
    if wrapped:
        result = {"responses": [result]}
    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=result), calls)
    output = asyncio.run(khc._call(ctx(), action, grievanceNo="BR/240926/2"))
    assert "BR/240926/2" in output and "OPEN" in output and khc.SOURCE in output
    sent = calls[0]
    assert sent["message"]["order"]["provider"]["id"] == "khc-agri"
    assert sent["message"]["order"]["items"][0]["id"] == "khc-grievance"
    assert sent["context"]["tags"] == {"session_id": "session-1", "question_id": "q1"}
    if action == "status":
        assert sent["message"]["order_id"] == sent["context"]["transaction_id"]


@pytest.mark.parametrize("action", ["status", "init"])
@pytest.mark.parametrize("response", [
    khc.httpx.Response(500, text="error"),
    khc.httpx.Response(200, text="not JSON"),
    khc.httpx.Response(200, json=[]),
    khc.httpx.Response(200, json={}),
    khc.httpx.ConnectError("unavailable"),
])
def test_network_failures_are_explicit_and_never_retried(monkeypatch, action, response):
    calls = []
    fake_http(monkeypatch, response, calls)
    output = asyncio.run(khc._call(ctx(), action, grievanceNo="BR/240926/2"))
    assert "not confirmed" in output if action == "init" else ("unavailable" in output or "could not be reached" in output)
    assert len(calls) == 1
    assert khc.SOURCE not in output


def test_missing_configuration_does_not_claim_submission(monkeypatch):
    monkeypatch.delenv("BAP_ENDPOINT", raising=False)
    assert "not configured" in asyncio.run(khc._call(ctx(), "init"))


@pytest.mark.parametrize("code", ["not_found", "submission_unconfirmed", "invalid_request"])
def test_returns_provider_errors_without_source(monkeypatch, code):
    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(code=code, message="Specific error")), calls)
    assert asyncio.run(khc._call(ctx(), "status", grievanceNo="BR/240926/2")) == "Specific error"


@pytest.mark.parametrize("fields", [{}, {"grievanceNo": "OTHER", "currentStatus": "OPEN"}])
def test_incomplete_or_wrong_grievance_is_not_reported_as_success(monkeypatch, fields):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(fields=fields)), [])
    assert "unavailable" in asyncio.run(khc._call(ctx(), "status", grievanceNo="BR/240926/2"))


def test_all_language_prompts_include_khc_rules():
    from helpers.utils import get_prompt

    for language in ("en", "hi", "as", "bn", "gu", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te"):
        prompt = get_prompt(f"agrinet_{language}", context={
            "today_date": "x", "crop_season": "x", "last_weekday_table": "",
            "vector_scheme_count": 0, "vector_schemes_bullets": "", "vector_schemes_identifiers": "",
        })
        assert "## Kisan Help Center (KHC) grievances" in prompt
        assert "check_khc_grievance_status" in prompt
        assert "register_khc_grievance" in prompt
        assert "initiate_khc_otp" in prompt
        assert "verify_khc_otp" in prompt
        assert "**Location reuse:**" in prompt
        assert "call `reverse_geocode`" in prompt
        assert "If browser coordinates are unavailable, ask the farmer for their state and village" in prompt
        assert "Never substitute a nearby village" in prompt


@pytest.mark.parametrize("coordinates,expected", [
    ({}, False),
    ({"latitude": 25.6, "longitude": 85.1}, True),
    ({"latitude": 25.6}, False),
])
def test_browser_location_is_available_to_khc_only_when_supplied(coordinates, expected):
    from agents.deps import FarmerContext

    message = FarmerContext(
        query="Register a KHC grievance", session_id="S1", lang_code="en", **coordinates,
    ).get_user_message()
    assert ("**Browser Location Coordinates:**" in message) is expected
    if expected:
        assert "Latitude=25.6, Longitude=85.1" in message


@pytest.mark.parametrize("mobile,session,expected", [
    ("", "S1", "mobile"), ("9876543210", "", "active conversation"),
])
def test_send_otp_requires_mobile_and_session(monkeypatch, mobile, session, expected):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert expected in asyncio.run(khc.initiate_khc_otp(ctx(session), mobile))
    call.assert_not_called()


@pytest.mark.parametrize("mobile,otp,session,expected", [
    ("", "123456", "S1", "mobile"), ("9876543210", "", "S1", "6-digit"),
    ("9876543210", "12345", "S1", "6-digit"), ("9876543210", "123456", "", "active conversation"),
])
def test_verify_otp_requires_valid_inputs(monkeypatch, mobile, otp, session, expected):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert expected in asyncio.run(khc.verify_khc_otp(ctx(session), mobile, otp))
    call.assert_not_called()


def test_otp_and_registration_share_transaction_without_exposing_otp(monkeypatch):
    responses = [
        envelope("init", "otp_sent", {}),
        envelope("init", "otp_verified", {"isRegistered": "false"}),
        envelope("init", "grievance_created"),
    ]
    calls = []
    real_client = khc.httpx.AsyncClient

    def respond(request):
        import json
        calls.append(json.loads(request.content))
        return khc.httpx.Response(200, json=responses.pop(0))

    monkeypatch.setenv("BAP_ENDPOINT", "https://bap.test")
    monkeypatch.setattr(khc.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=khc.httpx.MockTransport(respond), **kwargs,
    ))
    assert "OTP sent" in asyncio.run(khc.initiate_khc_otp(ctx(), "+91 9876543210"))
    result = asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "१२३४५६"))
    assert "not yet registered" in result
    assert "123456" not in result
    assert "BR/240926/2" in asyncio.run(khc.register_khc_grievance(
        ctx(), **registration(farmer_type="new", farmer_name="Test Farmer", gender="M"),
    ))
    assert len(calls) == 3
    assert len({call["context"]["transaction_id"] for call in calls}) == 1


@pytest.mark.parametrize("registered", ["true", "invalid"])
def test_verification_rendering_requires_registration_state(monkeypatch, registered):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "otp_verified", {"isRegistered": registered},
    )), [])
    result = asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "123456"))
    assert ("already registered" in result) if registered == "true" else ("unavailable" in result)


@pytest.mark.parametrize("kind,parent,page,name,expected", [
    ("unknown", None, 1, "", "valid KHC"), ("states", None, 0, "", "valid KHC"),
    ("schemes", None, 1, "", "parent stateId"), ("villages", True, 1, "", "parent subDistrictId"),
    ("states", None, 1, "x" * 201, "200 characters"),
])
def test_reference_validation(monkeypatch, kind, parent, page, name, expected):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert expected in asyncio.run(khc.lookup_khc_reference(ctx(), kind, parent, name, page))
    call.assert_not_called()


def test_reference_lookup_renders_verified_options(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_reference", {
        "options": '[{"scheme":38,"schemename":"AIF","division":31}]',
        "total": "1", "page": "1",
    })), [])
    output = asyncio.run(khc.lookup_khc_reference(ctx(), "schemes", 10, "AIF"))
    assert '"division": 31' in output and khc.SOURCE in output


def test_reference_malformed_options_do_not_reach_farmer(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_reference", {
        "options": "null",
    })), [])
    assert "unavailable" in asyncio.run(khc.lookup_khc_reference(ctx(), "states"))


def test_otp_failure_does_not_claim_grievance_submission(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(500), [])
    output = asyncio.run(khc.initiate_khc_otp(ctx(), "9876543210"))
    assert "unavailable" in output and "submission" not in output
