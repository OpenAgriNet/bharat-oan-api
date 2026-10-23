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


@pytest.mark.parametrize("name,code", [
    ("Hindi", "hi"), ("English", "en"), ("Marathi", "mr"), ("Bengali", "bn"),
    ("Gujarati", "gu"), ("Kannada", "kn"), ("Tamil", "ta"), ("Telugu", "te"),
    ("Assamese", "as"), ("Malayalam", "ml"),
])
def test_registration_normalizes_language_names_and_codes(monkeypatch, name, code):
    call = AsyncMock(return_value="Registered")
    monkeypatch.setattr(khc, "_call", call)
    for value in (f" {name} ", code.upper(), code):
        assert asyncio.run(khc.register_khc_grievance(
            ctx(), **registration(farmer_language=value),
        )) == "Registered"
        assert call.call_args.kwargs["farmerLanguage"] == code


@pytest.mark.parametrize("name,code", [
    ("male", "M"), ("FEMALE", "F"), (" Other ", "O"),
    ("m", "M"), ("f", "F"), ("o", "O"),
])
def test_new_farmer_normalizes_gender_and_preserves_external_reference(monkeypatch, name, code):
    call = AsyncMock(return_value="Registered")
    monkeypatch.setattr(khc, "_call", call)
    for language, gender in (("Hindi", name), ("hi", code)):
        assert asyncio.run(khc.register_khc_grievance(ctx(), **registration(
            farmer_type="new", farmer_name="Test Farmer",
            farmer_language=language, gender=gender,
        ))) == "Registered"
        assert call.call_args.kwargs["farmerLanguage"] == "hi"
        assert call.call_args.kwargs["gender"] == code
    assert call.call_args_list[0].kwargs["externalReferenceNo"] == call.call_args_list[1].kwargs["externalReferenceNo"]


def test_new_farmer_payload_matches_khc_field_contract(monkeypatch):
    call = AsyncMock(return_value="Registered")
    monkeypatch.setattr(khc, "_call", call)
    assert asyncio.run(khc.register_khc_grievance(ctx(), **registration(
        farmer_type="new", farmer_name="UAT New Farmer", gender="male",
        farmer_language="Hindi", grievance_text="Bank refused loan under AIF",
    ))) == "Registered"
    sent = call.call_args.kwargs
    assert sent == {
        "externalReferenceNo": sent["externalReferenceNo"],
        "farmerMobileNumber": "9876543210",
        "farmerName": "UAT New Farmer", "gender": "M", "farmerLanguage": "hi",
        "stateId": "10", "villageId": "219159", "scheme": "38", "division": "31",
        "problemCategory": "39", "problemSubCategory": "1725",
        "farmerText": "Bank refused loan under AIF",
        "farmerType": "new", "confirmed": "true", "request_type": "create_grievance",
    }
    assert sent["externalReferenceNo"].startswith("BV-")


@pytest.mark.parametrize("changes,expected", [
    ({"farmer_language": "French"}, "supported KHC language"),
    ({"farmer_language": "mai"}, "supported KHC language"),
    ({"farmer_language": "Maithili"}, "supported KHC language"),
    *[
        ({"farmer_language": language}, "supported KHC language")
        for language in (
            "Punjabi", "pa", "Odia", "or", "Urdu", "ur",
            "Nepali", "ne", "Manipuri", "mni",
        )
    ],
    ({"gender": "unknown"}, "name and gender"),
])
def test_registration_rejects_unsupported_language_or_gender(monkeypatch, changes, expected):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert expected in asyncio.run(khc.register_khc_grievance(ctx(), **registration(
        **{"farmer_type": "new", "farmer_name": "Test Farmer", "gender": "M", **changes},
    )))
    call.assert_not_called()


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
        assert "call `forward_geocode` first" in prompt
        assert "If browser coordinates are unavailable, ask \"Please share the name of your state\", then \"Can you please share your district name\", then \"Can you please share the name of your village\"" in prompt
        assert "Never substitute a nearby village" in prompt
        assert "**Problem-to-scheme matching:**" in prompt
        assert "If scheme is still missing, ask for scheme name" in prompt
        assert "Never ask for internal IDs/codes from the farmer" in prompt
        assert "a generic bank-loan complaint" in prompt
        assert "name` is a literal name substring filter" in prompt
        assert "include the matched scheme/category names in the final confirmation summary" in prompt
        assert "Do not proactively suggest KHC for unrelated questions" in prompt
        assert "If the grievance is for PM-Kisan, PMFBY, or AIF" in prompt
        assert "run the existing scheme grievance flow by default" in prompt
        assert "Use KHC for these schemes only when the user explicitly asks to use KHC" in prompt
        assert "keep that grievance in KHC flow for subsequent turns" in prompt
        assert "scheme without an existing grievance flow" in prompt
        assert "Kisan Helpline Center/Centre" in prompt
        assert "Do not ask for KHC opt-in again" in prompt
        assert "i want to create one kisan helpline center grivance" in prompt
        assert "start the KHC grievance flow" in prompt
        assert "**Sub-district (only when needed):**" in prompt
        assert "Never ask for or list sub-districts as a step of their own" in prompt
        assert "Ask which sub-district only when several villages in that district share the village name" in prompt
        assert "Resolve the village ID and all other required IDs before asking the farmer to confirm" in prompt
        assert "If the farmer-provided scheme, sub-district, village, category, or sub-category does not match a returned row" in prompt
        assert "AIF, A.I.F. and Agriculture Infrastructure Fund refer to the same scheme" in prompt
        assert "Do not ask the farmer to reconfirm AIF when already stated" in prompt
        assert "Pick the single best-fit category/sub-category automatically and proceed with that mapping" in prompt
        assert "Ask the farmer to choose from a returned list only when no supported fit can be determined automatically" in prompt
        assert "Do not offer to proceed with only a category" in prompt
        assert "When waiting for a KHC OTP, treat a six-digit reply as the OTP" in prompt
        assert "uttarpradesh" in prompt
        assert "Do not ask the farmer to resend a proper name just because of spacing or case" in prompt
        assert "Never repeat the same sentence, clause, or question in the same response" in prompt
        assert "Use simple wording" in prompt
        assert "**Scheme comes first:**" in prompt
        assert "Never ask the state, district or village while the scheme is unknown" in prompt
        assert "KHC is the same flow for every scheme (including PM-KISAN, PMFBY and AIF)" in prompt
        assert "never use the `pmkisan_*`, `pmfby_*` or AIF grievance tools" in prompt
        assert "Kisan Helpline Center first:" in prompt
        assert "Do not show the sub-category line" in prompt
        assert "call `get_khc_grievance_options` again with the same concern and `category` set to their pick" in prompt
        assert "Never choose a sub-category yourself" in prompt
        assert "Reference conversation (this is exactly how the flow must go" in prompt
        assert "Farmer: I want to create a grievance with KHC for AIF, I am from Uttarpradesh." in prompt
        assert "Bot: Please share the 6-digit OTP." in prompt
        assert "Only once the scheme, state, district, village and complaint are all known" in prompt
        assert "Never call this tool to find out what to ask next" in prompt
        assert "use that category and show just its sub-category names to choose from" in prompt
        assert "Ask, match, then list (every question, not only the problem)" in prompt
        assert "Show a list of names only when the tool says nothing matched or several matched" in prompt
        assert "Match the problem first, list only if nothing fits" in prompt
        assert "Never show category or sub-category names before the farmer has described the problem" in prompt
        assert "Warm tone (all KHC questions)" in prompt
        assert "State: \"Please share the name of your state\"" in prompt
        assert "District: \"Can you please share your district name\"" in prompt
        assert "Village: \"Can you please share the name of your village\"" in prompt
        assert "Mobile number: \"Please share your mobile number.\"" in prompt
        assert "OTP: \"Please share the 6-digit OTP.\"" in prompt
        assert "Do not start replies with \"Thank you\" or any filler" in prompt
        assert '3. District: "Can you please share your district name"' in prompt
        assert "7. Mobile number (only now, after the summary is confirmed;" in prompt
        assert "8. OTP: `initiate_khc_otp`, then `verify_khc_otp` (the last thing before submitting)." in prompt
        assert "Use what the farmer already said (do not ask again)" in prompt
        assert '"...with KHC for AIF, I am from Uttarpradesh" -> ask only "Can you please share your district name"' in prompt
        assert '"...for AIF, Uttar Pradesh, Meerut district" -> ask only "Can you please share the name of your village"' in prompt
        assert "Do not ask for anything the farmer already gave" in prompt
        assert "Summary with the scheme, the location (village, sub-district, district, state) and the category" in prompt
        assert "Never call it with the state or district name as the place, and never ask for the village before the district" in prompt
        assert "call `find_khc_location` again with the same village as `place`, the same state and district" in prompt
        assert "never ask for the village again" in prompt
        assert "KHC step order (follow exactly; every reply asks ONE question)" in prompt
        assert "Never ask for a later step early, never ask for two steps in one reply" in prompt
        assert "never ask for the mobile number or an OTP before the farmer has confirmed the summary" in prompt
        assert "end every reply with the line \"Source: Kisan Helpline Center\"" in prompt
        assert "Never put it between two sentences or before a question" in prompt
        assert "call `get_khc_grievance_options(state, scheme, concern)`" in prompt
        assert "Which one feels closest to your problem?" in prompt
        assert "Phone number and OTP come last" in prompt
        assert "Never write any text before calling a tool" in prompt
        assert "Never pick a scheme for the farmer" in prompt
        assert "At most 3 OTPs can be sent per hour" in prompt
        assert "A wrong OTP gives 5 tries" in prompt
        assert "ask which listed village is nearest and use it only if the farmer clearly says yes" in prompt
        assert "Do not submit again on your own" in prompt
        assert "Then call `find_khc_location(place=<village>, state=<state>, district=<district>)`" in prompt
        assert "Get scheme, category and sub-category with `get_khc_grievance_options`" in prompt
        assert "Please share the name of your state" in prompt
        assert "For every location question (state, district, sub-district/tehsil, village, or any other place)" in prompt
        assert "Never ask where \"the grievance\" or \"this complaint\" is from" in prompt
        assert "Location order: state, then district, then village." in prompt
        assert "If several villages in that district have that name, ask \"Could you please tell me which sub-district you are from?\"" in prompt
        assert "if it matches a district, use it as the district and do not ask for the district" in prompt
        assert "Do not offer another scheme such as e-NAM unless the farmer names it" in prompt
        assert "Avoid technical terms like \"mapping\", \"resolved\", \"determine automatically\"" in prompt
        assert "Complaint: \"Please tell me about your problem in your own words. I am here to help.\"" in prompt


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


@pytest.mark.parametrize("otp,expected", [
    ("654321", "654321"), (" 012345 ", "012345"), ("१२३४५६", "123456"),
])
def test_six_digit_otp_is_sent_to_verification_without_reasking(monkeypatch, otp, expected):
    call = AsyncMock(return_value="Mobile verified successfully.")
    monkeypatch.setattr(khc, "_call", call)
    assert "verified successfully" in asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", otp))
    call.assert_awaited_once_with(
        ctx(), "init", request_type="verify_otp", farmerMobileNumber="9876543210", otp=expected,
    )


def test_otp_sent_result_directs_verification_before_submission(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "otp_sent", {})), [])
    output = asyncio.run(khc.initiate_khc_otp(ctx(), "9876543210"))
    assert "call verify_khc_otp with the same mobile" in output
    assert "Do not reject a six-digit reply without a verification tool result" in output
    assert "proceed to submission after verification" not in output


@pytest.mark.parametrize("code,message", [
    ("invalid_otp", "OTP is invalid or expired"),
    ("upstream_error", "KHC service is unavailable"),
])
def test_verification_preserves_actual_failure_without_claiming_success(monkeypatch, code, message):
    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", code, {}, message=message,
    )), calls)
    assert asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "123456")) == message
    assert len(calls) == 1


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


def test_registration_otp_required_reuses_same_mobile_without_reasking(monkeypatch):
    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "otp_required", {}, message="OTP verification required",
    )), calls)
    output = asyncio.run(khc.register_khc_grievance(ctx(), **registration()))
    assert "same mobile number (9876543210)" in output
    assert "Do not ask the farmer for the mobile number again" in output
    assert len(calls) == 1


@pytest.mark.parametrize("registered", ["true", "invalid"])
def test_verification_rendering_requires_registration_state(monkeypatch, registered):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "otp_verified", {"isRegistered": registered},
    )), [])
    result = asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "123456"))
    assert ("already registered" in result) if registered == "true" else ("unavailable" in result)


@pytest.mark.parametrize("registered,asks_name", [("false", True), ("true", False)])
def test_verified_farmer_goes_straight_to_registration(monkeypatch, registered, asks_name):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "otp_verified", {"isRegistered": registered},
    )), [])
    result = asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "123456"))
    assert "This is the last step" in result
    assert "do not ask for them again" in result
    assert ("Ask only for the farmer's name and gender" in result) is asks_name
    assert "Call register_khc_grievance now" in result
    assert "lookup_khc_reference" not in result and "own words" not in result


def test_reference_results_direct_problem_matching_without_asking_for_ids(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_reference", {
        "options": '[{"scheme":38,"schemename":"Agriculture Infrastructure Fund (AIF)","division":31}]',
        "total": "1", "page": "1",
    })), [])
    output = asyncio.run(khc.lookup_khc_reference(ctx(), "schemes", 10))
    assert "Match the farmer's problem" in output
    assert "scheme and division from the same row" in output
    assert "categories" in output and "sub-categories" in output
    assert "unfiltered list and remaining pages" in output
    assert "Treat missing spaces and case differences as name variations" in output
    assert "show the relevant returned names for selection by name" in output
    assert "Pick the single best-fit category/sub-category automatically and proceed with that mapping" in output


@pytest.mark.parametrize("kind,parent,options", [
    ("schemes", 9, '[{"scheme":38,"schemename":"Agriculture Infrastructure Fund (AIF)","division":31}]'),
    ("sub-categories", 39, '[{"problemsubcategory":1725,"subcategoryname":"Bank refused loan"}]'),
    ("sub-categories", 39, "[]"),
])
def test_reference_guidance_reuses_named_scheme_and_blocks_incomplete_confirmation(
    monkeypatch, kind, parent, options,
):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_reference", {
        "options": options, "total": "0" if options == "[]" else "1", "page": "1",
    })), [])
    output = asyncio.run(khc.lookup_khc_reference(ctx(), kind, parent))
    assert "AIF, A.I.F. and Agriculture Infrastructure Fund refer to the same scheme" in output
    assert "Do not ask the farmer to reconfirm AIF when already stated" in output
    assert "Pick the single best-fit category/sub-category automatically" in output
    assert "choose from the returned sub-category names only when no supported fit" in output
    assert "Do not offer to proceed with only a category" in output
    assert "Never claim readiness or ask for final submission confirmation until all required IDs are resolved" in output


@pytest.mark.parametrize("options,total,page", [
    ('[{"subdistrictid":"1067","subdistrictname":"Test Tehsil"}]', "1", "1"),
    ('[{"subdistrictid":"1068","subdistrictname":"Another Tehsil"}]', "51", "2"),
    ("[]", "0", "1"),
])
def test_location_reference_results_guide_api_backed_subdistrict_selection(
    monkeypatch, options, total, page,
):
    import json

    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_reference", {
        "options": options, "total": total, "page": page,
    })), calls)
    output = asyncio.run(khc.lookup_khc_reference(
        ctx(), "sub-districts", 221, page=int(page),
    ))
    rendered_options = output.split(" options: ", 1)[1].split("\n", 1)[0]
    assert json.loads(rendered_options) == json.loads(options)
    assert f"Total matches: {total}; page: {page}" in output
    assert "Do not list sub-districts as a step: ask for the village first" in output
    assert "select by name only" in output
    assert "Never invent options or present an empty list as a choice" in output
    assert "fetch villages under the selected subdistrictid" in output
    assert "use forward_geocode and reverse_geocode to derive missing district/state fields" in output
    assert "If the district is unknown, ask for it first" in output
    assert "Resolve the village ID before asking for final submission confirmation" in output
    assert len(calls) == 1


def test_reference_hierarchy_ids_are_forwarded_to_registration_payload(monkeypatch):
    import json

    reference_rows = [
        ("states", None, {"stateid": 10, "statename": "Bihar"}),
        ("districts", 10, {"districtid": 221, "districtname": "Test District"}),
        ("sub-districts", 221, {"subdistrictid": "1067", "subdistrictname": "Test Sub-district"}),
        ("villages", 1067, {"villageid": 219159, "villagename": "Test Village"}),
        ("schemes", 10, {"scheme": 38, "schemename": "Agriculture Infrastructure Fund (AIF)", "division": 31}),
        ("categories", 38, {"problemcategory": 39, "categoryname": "Loan"}),
        ("sub-categories", 39, {"problemsubcategory": 1725, "subcategoryname": "Bank refused loan"}),
    ]
    responses = [envelope("init", "khc_reference", {
        "options": json.dumps([row]), "total": "1", "page": "1",
    }) for _, _, row in reference_rows] + [envelope("init", "grievance_created")]
    calls = []
    real_client = khc.httpx.AsyncClient

    def respond(request):
        calls.append(json.loads(request.content))
        return khc.httpx.Response(200, json=responses.pop(0))

    monkeypatch.setenv("BAP_ENDPOINT", "https://bap.test")
    monkeypatch.setattr(khc.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=khc.httpx.MockTransport(respond), **kwargs,
    ))
    resolved = {}
    for kind, parent, _ in reference_rows:
        output = asyncio.run(khc.lookup_khc_reference(ctx(), kind, parent))
        options = json.loads(output.split(" options: ", 1)[1].split("\n", 1)[0])
        resolved[kind] = options[0]

    output = asyncio.run(khc.register_khc_grievance(ctx(), **registration(
        state_id=resolved["states"]["stateid"],
        village_id=resolved["villages"]["villageid"],
        scheme_id=resolved["schemes"]["scheme"],
        division_id=resolved["schemes"]["division"],
        problem_category_id=resolved["categories"]["problemcategory"],
        problem_subcategory_id=resolved["sub-categories"]["problemsubcategory"],
    )))
    assert "BR/240926/2" in output
    for call, (kind, parent, _) in zip(calls, reference_rows):
        tags = {
            tag["descriptor"]["code"]: tag["value"]
            for tag in call["message"]["order"]["fulfillments"][0]["customer"]["person"]["tags"]
        }
        assert tags["reference_type"] == kind
        assert tags.get("parent_id") == (str(parent) if parent is not None else None)
        assert "name" not in tags  # No full complaint passed as a substring filter.
    submitted = {
        tag["descriptor"]["code"]: tag["value"]
        for tag in calls[-1]["message"]["order"]["fulfillments"][0]["customer"]["person"]["tags"]
    }
    assert {key: submitted[key] for key in khc._MASTER_FIELDS} == {
        "stateId": "10", "villageId": "219159", "scheme": "38", "division": "31",
        "problemCategory": "39", "problemSubCategory": "1725",
    }


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


def _tags(call):
    return {
        tag["descriptor"]["code"]: tag["value"]
        for tag in call["message"]["order"]["fulfillments"][0]["customer"]["person"]["tags"]
    }


@pytest.mark.parametrize("place,state,district", [("", "", ""), ("a" * 201, "", ""), ("x", "s" * 101, "")])
def test_find_location_validates_input_without_http(monkeypatch, place, state, district):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert "village or place name" in asyncio.run(khc.find_khc_location(ctx(), place, state, district))
    call.assert_not_called()


def test_find_location_sends_known_details_and_reports_one_village(monkeypatch):
    import json

    village = {
        "level": "village", "id": 5, "name": "Abupur",
        "state": {"id": 9, "name": "Uttar Pradesh"}, "district": {"id": 221, "name": "Meerut"},
        "subDistrict": {"id": 1067, "name": "Meerut Block"},
    }
    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps([village]), "total": "1",
    })), calls)
    output = asyncio.run(khc.find_khc_location(ctx(), " Abupur ", "uttarpredesh", ""))
    assert _tags(calls[0]) == {
        "request_type": "find_location", "name": "Abupur", "state_name": "uttarpredesh",
    }
    assert "Abupur - Meerut Block, Meerut, Uttar Pradesh" in output
    assert "stateId=9, districtId=221, subDistrictId=1067, villageId=5" in output
    assert "do not ask the farmer for state, district, sub-district or village again" in output
    assert khc.SOURCE in output


@pytest.mark.parametrize("needs,expected", [
    ("state", "Please share the name of your state"),
    ("district", "Can you please share your district name"),
    ("geocode", "forward_geocode"),
])
def test_find_location_without_a_match_says_what_to_ask(monkeypatch, needs, expected):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": "[]", "needs": json.dumps(needs),
    })), [])
    assert expected in asyncio.run(khc.find_khc_location(ctx(), "nowhere"))


def test_find_location_offers_sub_districts_when_several_villages_share_the_name(monkeypatch):
    import json

    base = {"level": "village", "name": "Abupur", "state": {"id": 9, "name": "Uttar Pradesh"}}
    rows = [{**base, "id": 5, "district": {"id": 221, "name": "Meerut"}, "subDistrict": {"id": 1, "name": "Meerut Block"}},
            {**base, "id": 7, "district": {"id": 222, "name": "Ghaziabad"}, "subDistrict": {"id": 2, "name": "Loni"}}]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps(rows), "total": "2",
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "abupur", "Uttar Pradesh"))
    assert "Could you please tell me which sub-district you are from?" in output
    assert "- Abupur: Meerut Block sub-district, Meerut district" in output  # different districts: show both
    assert "- Abupur: Loni sub-district, Ghaziabad district" in output
    assert "block" not in output.lower().replace("meerut block", "")
    assert "call this tool again with place='abupur', the same state and district, and sub_district=<their pick>" in output
    assert "do not ask for the village again" in output
    assert "villageId=5" in output and "villageId=7" in output
    assert "list was cut" not in output


def test_find_location_asks_for_district_only_when_the_list_was_cut(monkeypatch):
    import json

    base = {"level": "village", "name": "Rampur", "state": {"id": 9, "name": "UP"},
            "district": {"id": 221, "name": "Meerut"}}
    rows = [{**base, "id": i, "subDistrict": {"id": i, "name": f"B{i}"}} for i in range(10)]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps(rows), "total": "51",
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "rampur", "Uttar Pradesh"))
    assert "The list was cut" in output
    assert "type their sub-district name" in output and "as sub_district" in output


def test_find_location_malformed_result_is_not_trusted(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": "not json",
    })), [])
    assert "unavailable" in asyncio.run(khc.find_khc_location(ctx(), "abupur"))


@pytest.mark.parametrize("state,scheme", [("", ""), ("s" * 101, ""), ("Bihar", "x" * 101)])
def test_grievance_options_validates_input_without_http(monkeypatch, state, scheme):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    assert "state" in asyncio.run(khc.get_khc_grievance_options(ctx(), state, scheme))
    call.assert_not_called()


def test_grievance_options_returns_categories_and_sub_categories_for_a_complaint(monkeypatch):
    import json

    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 38, "name": "Agriculture Infrastructure Fund (AIF)", "division": 31}),
        "categories": json.dumps([{
            "id": 39, "name": "Bank Loan Sanction Issues",
            "subCategories": [{"id": 1725, "name": "Bank refused loan"}],
        }]),
    })), calls)
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "A.I.F.", "my vehicle was stolen"))
    assert _tags(calls[0]) == {
        "request_type": "grievance_tree", "state_name": "Uttar Pradesh", "scheme_name": "A.I.F.",
        "concern": "my vehicle was stolen",
    }
    assert "schemeId=38, divisionId=31" in output
    assert "No clear match for the complaint" in output
    assert "- Bank Loan Sanction Issues" in output
    assert "Bank refused loan" not in output and "problemCategoryId" not in output  # nothing to guess from


def test_grievance_options_without_a_complaint_shows_no_categories(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 38, "name": "AIF", "division": 31}),
        "categories": json.dumps([{"id": 39, "name": "Bank Loan Sanction Issues",
                                   "subCategories": [{"id": 1725, "name": "Bank refused loan"}]}]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "AIF"))
    assert "Nothing to show yet" in output
    assert "Do not show any list, and do not ask anything because of this message" in output
    assert "ask the next step that is not done yet" in output
    assert "Bank Loan Sanction Issues" not in output and "Bank refused loan" not in output
    assert "problemCategoryId" not in output


def test_grievance_options_does_not_list_schemes_before_the_farmer_names_one(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}), "needs": json.dumps("scheme"),
        "match": json.dumps("unnamed"),
        "schemes": json.dumps([{"id": 38, "name": "AIF", "division": 31}]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh"))
    assert "has not named a scheme yet" in output and "Do not show a list" in output
    assert "AIF" not in output
    assert 'reference_type="schemes", parent_id=9' in output


def test_grievance_options_lists_only_the_schemes_that_matched(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}), "needs": json.dumps("scheme"),
        "match": json.dumps("several"),
        "schemes": json.dumps([{"id": 38, "name": "AIF", "division": 31}]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "a"))
    assert "More than one scheme matched" in output and "- AIF" in output


def test_find_location_lists_districts_when_the_district_did_not_match(monkeypatch):
    import json

    state = {"id": 9, "name": "Uttar Pradesh"}
    rows = [{"level": "district", "id": 221, "name": "Ghaziabad"}, {"level": "district", "id": 222, "name": "Ghazipur"}]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "needs": json.dumps("district"), "state": json.dumps(state), "matches": json.dumps(rows),
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "abupur", "Uttar Pradesh", "ghazi"))
    assert "did not match exactly, or matched more than one" in output
    assert "- Ghaziabad" in output and "- Ghazipur" in output
    assert "Then call this tool again with their pick as district" in output


def test_new_khc_tools_are_registered_in_the_khc_capability():
    from agents.capability_manifest import CAPABILITIES

    assert {"find_khc_location", "get_khc_grievance_options"} <= CAPABILITIES["khc"]


def test_rate_limited_message_reaches_the_model(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "rate_limited", {}, message="Please wait 20 seconds before asking for a new OTP.",
    )), [])
    assert asyncio.run(khc.initiate_khc_otp(ctx(), "9876543210")) == (
        "Please wait 20 seconds before asking for a new OTP."
    )


def test_scheme_not_offered_by_khc_is_explained(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}), "needs": json.dumps("scheme"),
        "match": json.dumps("none"),
        "schemes": json.dumps([{"id": 38, "name": "AIF", "division": 31}]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "Rythu Bandhu"))
    assert "'Rythu Bandhu' is not a scheme KHC takes complaints for in this state" in output
    assert "nearest agriculture office" in output and "Do not pick a scheme for them" in output
    assert "- AIF" in output


def test_place_not_found_gives_sub_district_and_village_fallback_with_the_district_id(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": "[]", "needs": json.dumps("geocode"), "district": json.dumps({"id": 221, "name": "Meerut"}),
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "nowhere", "Uttar Pradesh", "Meerut"))
    assert 'reference_type="sub-districts", parent_id=221' in output
    assert "Could you please tell me which sub-district you are from?" in output
    assert "write their real village name in the complaint text" in output


def test_tree_shows_the_suggested_match_and_how_to_change_it(monkeypatch):
    import json

    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 38, "name": "AIF", "division": 31}),
        "suggested": json.dumps({"category": {"id": 39, "name": "Bank Loan Sanction Issues"},
                                 "subCategory": {"id": 1725, "name": "Bank refused loan"}}),
        "categories": json.dumps([{"id": 39, "name": "Bank Loan Sanction Issues",
                                   "subCategories": [{"id": 1725, "name": "Bank refused loan"}]}]),
    })), calls)
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "AIF", "  bank   refused my loan "))
    assert _tags(calls[0])["concern"] == "bank refused my loan"
    assert "Best match for the complaint: category 'Bank Loan Sanction Issues'" in output
    assert "sub-category 'Bank refused loan' [problemSubCategoryId=1725]" in output
    assert "show the category only; do not show the sub-category" in output


def test_tree_with_a_complaint_but_no_match_lists_the_categories(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 38, "name": "AIF", "division": 31}),
        "categories": json.dumps([{"id": 39, "name": "Bank Loan Sanction Issues",
                                   "subCategories": [{"id": 1725, "name": "Bank refused loan"}]}]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "AIF", "rain damaged my crop"))
    assert "No clear match for the complaint. Do not guess." in output
    assert "- Bank Loan Sanction Issues" in output
    assert "Bank refused loan" not in output and "[problemCategoryId" not in output
    assert "call this tool again with the same concern and category=<their pick>" in output
    assert "Never choose a sub-category yourself" in output


def test_complaint_longer_than_500_characters_is_rejected_without_http(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    asyncio.run(khc.get_khc_grievance_options(ctx(), "Bihar", "AIF", "x" * 501))
    call.assert_not_called()


def test_find_location_sends_the_picked_sub_district(monkeypatch):
    import json

    calls = []
    village = {"level": "village", "id": 8, "name": "Abupur", "state": {"id": 9, "name": "Uttar Pradesh"},
               "district": {"id": 222, "name": "Ghaziabad"}, "subDistrict": {"id": 2001, "name": "Modinagar"}}
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps([village]), "total": "1",
    })), calls)
    output = asyncio.run(khc.find_khc_location(ctx(), "abupur", "Uttar Pradesh", "", " Modinagar "))
    assert _tags(calls[0]) == {
        "request_type": "find_location", "name": "abupur", "state_name": "Uttar Pradesh",
        "sub_district_name": "Modinagar",
    }
    assert "Found one place" in output and "villageId=8" in output
    assert "Still ask" not in output


@pytest.mark.parametrize("place,state", [("uttar pradesh", "Uttar Pradesh"), ("UttarPradesh", "uttar pradesh")])
def test_find_location_refuses_the_state_name_as_a_place(monkeypatch, place, state):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    output = asyncio.run(khc.find_khc_location(ctx(), place, state))
    assert "That is the state, not a village" in output
    assert "Can you please share your district name" in output and "Can you please share the name of your village" in output
    call.assert_not_called()


def test_a_district_and_sub_district_of_the_same_name_are_not_listed_as_villages(monkeypatch):
    import json

    state = {"id": 9, "name": "Uttar Pradesh"}
    district = {"id": 222, "name": "Ghaziabad"}
    rows = [
        {"level": "district", "id": 222, "name": "Ghaziabad", "state": state, "district": district, "subDistrict": None},
        {"level": "sub-district", "id": 2001, "name": "Ghaziabad", "state": state, "district": district,
         "subDistrict": {"id": 2001, "name": "Ghaziabad"}},
    ]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps(rows), "total": "2",
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "ghaziabad", "Uttar Pradesh"))
    assert "is the name of a district or sub-district, not a village" in output
    assert "ask 'Can you please share the name of your village'" in output
    assert "Do not list these as villages" in output
    assert "Could you please tell me which sub-district you are from?" not in output


def test_a_village_match_beats_a_district_of_the_same_name(monkeypatch):
    import json

    state = {"id": 9, "name": "Uttar Pradesh"}
    district = {"id": 222, "name": "Ghaziabad"}
    rows = [
        {"level": "district", "id": 222, "name": "Ghaziabad", "state": state, "district": district, "subDistrict": None},
        {"level": "village", "id": 5, "name": "Ghaziabad", "state": state, "district": {"id": 9, "name": "Meerut"},
         "subDistrict": {"id": 1, "name": "Daurala"}},
    ]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps(rows), "total": "2",
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "ghaziabad", "Uttar Pradesh"))
    assert "Found one place" in output and "villageId=5" in output
    assert "districtId=222" not in output


def test_find_location_refuses_the_district_name_as_a_place(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(khc, "_call", call)
    output = asyncio.run(khc.find_khc_location(ctx(), "Meerut", "Uttar Pradesh", "meerut"))
    assert "That is the district, not a village" in output
    assert "Can you please share the name of your village" in output
    call.assert_not_called()


def test_sub_district_options_omit_the_district_when_they_share_one(monkeypatch):
    import json

    base = {"level": "village", "name": "Abupur", "state": {"id": 9, "name": "Uttar Pradesh"},
            "district": {"id": 222, "name": "Ghaziabad"}}
    rows = [{**base, "id": 5, "subDistrict": {"id": 1, "name": "Loni"}},
            {**base, "id": 7, "subDistrict": {"id": 2, "name": "Modinagar"}}]
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_location", {
        "matches": json.dumps(rows), "total": "2",
    })), [])
    output = asyncio.run(khc.find_khc_location(ctx(), "abupur", "Uttar Pradesh", "Ghaziabad"))
    assert "- Abupur: Loni sub-district\n" in output
    assert "- Abupur: Modinagar sub-district\n" in output
    assert "sub-district, Ghaziabad district" not in output


def test_a_named_category_with_an_unclear_sub_category_offers_only_its_sub_categories(monkeypatch):
    import json

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 38, "name": "AIF", "division": 31}),
        "suggested": json.dumps({"category": {"id": 39, "name": "Bank Loan Sanction Issues"}, "subCategory": None}),
        "categories": json.dumps([
            {"id": 39, "name": "Bank Loan Sanction Issues", "subCategories": [
                {"id": 1725, "name": "Bank refused loan"}, {"id": 1726, "name": "Loan sanction delay"}]},
            {"id": 41, "name": "Fund Disbursement Issues", "subCategories": [{"id": 1801, "name": "Subsidy not received"}]},
        ]),
    })), [])
    output = asyncio.run(khc.get_khc_grievance_options(ctx(), "Uttar Pradesh", "AIF", "bank loan sanction issue"))
    assert "The problem is under the category 'Bank Loan Sanction Issues'" in output
    assert "- Bank refused loan [problemSubCategoryId=1725]" in output
    assert "- Loan sanction delay [problemSubCategoryId=1726]" in output
    assert "Which one feels closest to your problem?" in output
    assert "Do not choose one yourself" in output and "Do not show the sub-category in the summary" in output
    assert "Fund Disbursement Issues" not in output and "Subsidy not received" not in output
    assert "No clear match" not in output


def test_picked_category_is_sent_to_the_provider(monkeypatch):
    import json

    calls = []
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", "khc_grievance_tree", {
        "state": json.dumps({"id": 9, "name": "Uttar Pradesh"}),
        "scheme": json.dumps({"id": 40, "name": "PM-KISAN", "division": 32}),
        "suggested": json.dumps({"category": {"id": 50, "name": "Delay in receiving benefit"},
                                 "subCategory": {"id": 501, "name": "Installment not received"}}),
        "categories": json.dumps([{"id": 50, "name": "Delay in receiving benefit",
                                   "subCategories": [{"id": 501, "name": "Installment not received"}]}]),
    })), calls)
    output = asyncio.run(khc.get_khc_grievance_options(
        ctx(), "Uttar Pradesh", "PM Kisan", "installment not received", " Delay in receiving benefit "))
    assert _tags(calls[0])["category_name"] == "Delay in receiving benefit"
    assert _tags(calls[0])["concern"] == "installment not received"
    assert "sub-category 'Installment not received' [problemSubCategoryId=501]" in output
    assert "do not show the sub-category" in output
