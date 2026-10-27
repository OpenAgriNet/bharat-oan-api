import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.test_production_reconciliation import load_module

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("as", "bn", "en", "gu", "hi", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te")
TOOLS = {"initiate_kcc_otp", "check_kcc_application_status", "select_kcc_application"}


@pytest.fixture
def kcc(load_module, monkeypatch):
    pmkisan = load_module("agents/tools/pmkisan_scheme_status.py")
    monkeypatch.setitem(sys.modules, "agents.tools.pmkisan_scheme_status", pmkisan)
    aif = load_module("agents/tools/aif.py")
    monkeypatch.setitem(sys.modules, "agents.tools.aif", aif)
    monkeypatch.setenv("BAP_ENDPOINT", "https://example.test")
    return load_module("agents/tools/kcc.py")


@pytest.fixture
def ctx():
    return SimpleNamespace(deps=SimpleNamespace(session_id="test-session", question_id="test-question"))


def tag(code, description="", **values):
    return {
        "descriptor": {"code": code, "short_desc": description},
        "list": [{"descriptor": {"code": key}, "value": value} for key, value in values.items()],
    }


def envelope(code, description="", **values):
    return {"message": {"order": {"tags": [tag(code, description, **values)]}}}


@pytest.mark.parametrize("language", LANGUAGES)
def test_localized_kcc_and_mandi_routing(language):
    prompt = (ROOT / "assets/prompts" / f"agrinet_{language}.md").read_text()
    assert all(tool in prompt for tool in TOOLS)
    assert "NO_DATA_AT_REQUESTED_MANDI" in prompt
    assert "include_nearby_mandis=true" in prompt
    assert "RANGE_TOO_LONG" in prompt
    assert 'get_scheme_info("kcc")' in prompt
    assert "price_date_to" in prompt
    assert "**A different mobile number is a new check.**" in prompt
    assert "load_capability" not in prompt
    assert "get_agristack_farmer" not in prompt
    assert "Never offer status checks for KCC" not in prompt


def test_kcc_tools_are_registered_without_development_features():
    tree = ast.parse((ROOT / "agents/tools/__init__.py").read_text())
    registered = {
        node.args[0].id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Tool" and isinstance(node.args[0], ast.Name)
    }
    assert TOOLS <= registered
    assert not any(name.startswith(("get_agristack_", "initiate_khc_")) for name in registered)
    assert "imd_agromet_advisory" not in registered


def test_kcc_status_service_route():
    tree = ast.parse((ROOT / "helpers/scheme_qdrant_search.py").read_text())
    services = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name) and node.target.id == "SCHEME_STATUS_SERVICES")
    assert ast.literal_eval(services.value)["kcc"] == (
        ("KCC application status", "initiate_kcc_otp \u2192 check_kcc_application_status"),
    )


def test_kcc_tool_schemas_build(kcc):
    from pydantic_ai import Tool
    for name in TOOLS:
        tool = Tool(getattr(kcc, name), takes_ctx=True, strict=False)
        assert "mobile_number" in tool.function_schema.json_schema["properties"]


@pytest.mark.parametrize("mobile", ["9876543210", "+91 98765-43210", "919876543210", "09876543210"])
def test_mobile_normalization(kcc, mobile):
    assert kcc._mobile(mobile) == "9876543210"


@pytest.mark.parametrize("tool,args,expected", [
    ("initiate_kcc_otp", ("123456",), "NEED_MOBILE"),
    ("check_kcc_application_status", ("invalid", "123456"), "NEED_MOBILE"),
    ("check_kcc_application_status", ("9876543210", "123"), "NEED_OTP"),
    ("select_kcc_application", ("invalid", "100"), "NEED_MOBILE"),
    ("select_kcc_application", ("9876543210", "letters"), "NEED_APPLICATION_NO"),
])
def test_invalid_input_never_reaches_network(kcc, ctx, monkeypatch, tool, args, expected):
    monkeypatch.setattr(kcc.httpx, "post", lambda *a, **kw: pytest.fail("unexpected network call"))
    assert getattr(kcc, tool)(ctx, *args) == getattr(kcc, expected)


def test_otp_status_and_selection_use_same_transaction_without_reusing_otp(kcc, ctx, monkeypatch):
    requests = []
    responses = iter([
        {"message": {"order": {"items": [{"tags": [tag(
            "otp_sent", "OTP sent", masked_mobile="XXXXXX3210", expires_in="15 minutes"
        )]}]}}},
        {"responses": [envelope("application_status", farmer_name="Test Farmer",
                               application_no="100", status="APPROVED",
                               required_loan_amount="100000", sanctioned_amount="90000",
                               bank_name="Test Bank", branch_name="Test Branch", remark="Approved")]},
        envelope("application_status", application_no="101", status="PENDING"),
    ])

    def post(url, *, json, timeout):
        requests.append((url, json))
        return httpx.Response(200, json=next(responses))

    monkeypatch.setattr(kcc.httpx, "post", post)
    assert "Sent to mobile: XXXXXX3210" in kcc.initiate_kcc_otp(ctx, "+91 9876543210")
    result = kcc.check_kcc_application_status(ctx, "9876543210", "123456")
    assert "approved by Test Bank Test Branch" in result
    assert "1,00,000" in result and "90,000" in result
    assert "Remarks: Approved" in result
    assert "101 is currently PENDING" in kcc.select_kcc_application(ctx, "9876543210", "101")
    assert [url for url, _ in requests] == [
        "https://example.test/init", "https://example.test/status", "https://example.test/status"
    ]
    assert len({payload["context"]["transaction_id"] for _, payload in requests}) == 1
    for _, payload in requests:
        assert payload["context"]["tags"] == {"session_id": "test-session", "question_id": "test-question"}
        assert payload["message"]["order"]["provider"]["id"] == "kcc-agri"
        if payload["context"]["action"] == "status":
            assert payload["message"]["order_id"] == payload["context"]["transaction_id"]
    selection = requests[-1][1]
    tags = selection["message"]["order"]["fulfillments"][0]["customer"]["person"]["tags"]
    assert {t["descriptor"]["code"]: t["value"] for t in tags} == {
        "request_type": "application_status", "mobile_number": "9876543210", "application_no": "101"
    }
    assert kcc.generate_transaction_id(ctx.deps.session_id, "9876543210") != kcc.generate_transaction_id(
        ctx.deps.session_id, "9876543211"
    )


def test_otp_redaction_does_not_mutate_request(kcc, ctx):
    payload = kcc._build_payload("status", "transaction", ctx, otp="123456")
    assert "123456" not in str(kcc._loggable(payload))
    assert "123456" in str(payload)


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "json", "config"])
def test_request_failures_are_reported(kcc, ctx, monkeypatch, caplog, failure):
    def post(*args, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout")
        if failure == "connection":
            raise httpx.ConnectError("test connection")
        return httpx.Response(503) if failure == "http" else httpx.Response(200, text="not json")
    monkeypatch.setattr(kcc.httpx, "post", post)
    if failure == "config":
        monkeypatch.delenv("BAP_ENDPOINT")
    assert kcc.initiate_kcc_otp(ctx, "9876543210") == kcc.UNAVAILABLE
    assert caplog.records


@pytest.mark.parametrize("tool,args", [
    ("check_kcc_application_status", ("9876543210", "123456")),
    ("select_kcc_application", ("9876543210", "100")),
])
def test_status_request_failure_is_not_a_success(kcc, ctx, monkeypatch, tool, args):
    monkeypatch.setattr(kcc.httpx, "post", lambda *a, **kw: httpx.Response(503))
    assert getattr(kcc, tool)(ctx, *args) == kcc.UNAVAILABLE


def test_multiple_applications_prompt_for_selection(kcc):
    response = envelope("multiple_applications")
    response["message"]["order"]["items"] = [
        {"tags": [tag("application", application_no="100", status="APPROVED", required_loan_amount="50000")]},
        {"tags": [tag("application", application_no="101", status="PENDING")]},
    ]
    result = kcc._render_application_status(response)
    assert "2 KCC applications" in result
    assert "100 (APPROVED" in result and "101 (PENDING)" in result
    assert "select_kcc_application" in result and "no new OTP" in result


@pytest.mark.parametrize("status", ["REJECTED", "DRAFT"])
def test_rejection_is_not_reported_as_approval(kcc, status):
    result = kcc._render_application({
        "application_no": "100", "status": status, "rejected_by": "Test Reviewer",
        "rejection_reason": "Missing document", "required_loan_amount": "12000.50",
    })
    assert "was rejected by Test Reviewer due to Missing document" in result
    assert "12,000.50" in result
    assert ("Drafts section" in result) == (status == "DRAFT")


@pytest.mark.parametrize("code,description", [
    ("no_applications", "No applications found"),
    ("invalid_otp", "Invalid OTP"),
    ("session_expired", "Session expired"),
    ("unexpected", ""),
])
def test_provider_status_errors_are_preserved(kcc, code, description):
    result = kcc._render_application_status(envelope(code, description))
    assert description in result if description else result == kcc.UNAVAILABLE


def test_empty_and_rejected_otp_responses(kcc):
    assert kcc._render_otp_sent({}) == kcc.UNAVAILABLE
    response = {"message": {"order": {"items": [{"tags": [tag("invalid_mobile", "No application")]}]}}}
    assert kcc._render_otp_sent(response) == "No application"
    assert kcc._render_application({}).endswith("is currently under process.")
    assert kcc._rupees("not available") == "not available"
