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
        assert "If browser coordinates are unavailable, ask the farmer for village/place name first" in prompt
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
        assert "**Sub-district selection:**" in prompt
        assert 'reference_type="sub-districts", parent_id=<resolved districtId>, name=""' in prompt
        assert "show a bulleted list of the returned subdistrictname values" in prompt
        assert "select by name only, not by number" in prompt
        assert "If the district cannot be established from the village details, ask the farmer for the district" in prompt
        assert "Resolve the village ID and all other required mappings before asking for final submission confirmation" in prompt
        assert "If the farmer-provided scheme, sub-district, village, category, or sub-category does not match a returned row" in prompt
        assert "AIF, A.I.F. and Agriculture Infrastructure Fund refer to the same scheme" in prompt
        assert "Do not ask the farmer to reconfirm AIF when already stated" in prompt
        assert "Do not offer to proceed with only a category" in prompt
        assert "When waiting for a KHC OTP, treat a six-digit reply as the OTP" in prompt
        assert "uttarpradesh" in prompt
        assert "Do not ask the farmer to resend a proper name just because of spacing or case" in prompt
        assert "collect scheme + location + concern -> map category/sub-category" in prompt
        assert "show summary with mapped sub-category -> allow sub-category change" in prompt
        assert "do not ask for mobile number in the first response" in prompt
        assert "Ask for mobile number only after the user confirms the final grievance summary and sub-category" in prompt
        assert "Do not call `initiate_khc_otp` until summary and sub-category are confirmed" in prompt
        assert "If scheme is missing, ask scheme first" in prompt
        assert "Do not ask for phone number, OTP, or registration status in this opening turn" in prompt
        assert "Ask only the next missing step from the registration order in each turn" in prompt
        assert "Follow collection order exactly: scheme -> location -> concern" in prompt
        assert "If scheme is missing, ask only for scheme and wait" in prompt


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
    assert "If scheme, location, concern and sub-category summary are already confirmed" in output


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


def test_verified_farmer_is_directed_to_resolve_scheme_from_problem(monkeypatch):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope(
        "init", "otp_verified", {"isRegistered": "false"},
    )), [])
    result = asyncio.run(khc.verify_khc_otp(ctx(), "9876543210", "123456"))
    assert "problem in their own words" in result
    assert "lookup_khc_reference" in result
    assert "ask for the scheme name" in result
    assert "Never ask for internal IDs/codes from the farmer" in result


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
    assert "show the relevant returned sub-category names for selection by name" in output
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
    assert "show a bulleted list of returned sub-district names" in output
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
