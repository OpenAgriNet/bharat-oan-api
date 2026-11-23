"""Session state written by the PMFBY status and grievance tools (section "pmfby")."""
from types import SimpleNamespace

import httpx
import pytest

from agents.deps import FarmerContext
from agents.tools import pmfby_grievance, pmfby_scheme_status

CTX = {"action": "on_status", "timestamp": "t", "message_id": "m", "transaction_id": "tx",
       "domain": "schemes:vistaar", "version": "1.1.0"}
PHONE = "9876543210"


def make_ctx():
    return SimpleNamespace(deps=FarmerContext(session_id="s", query="q"))


def state(ctx):
    return ctx.deps.session_state.get("pmfby", {})


def order_response(order, message_extra=None):
    return {"context": CTX, "responses": [{"context": CTX, "message": {"order": order, **(message_extra or {})}}]}


POLICY = order_response({"provider": {"id": "pmfby"}, "items": [{"id": "1", "tags": [
    {"descriptor": {"code": "policy", "name": "Policy"}, "value": "P-1"}]}]})
NOT_FOUND = order_response({"items": [{"id": "1", "tags": [{"descriptor": {"code": "not_found", "name": "Error"}}]}]})
OTP_VERIFIED = order_response({"state": "COMPLETED", "tags": [{"descriptor": {"code": "otp_verified"}, "value": "true"}]})
OTP_INVALID = order_response({"id": "error", "tags": [{"descriptor": {"code": "invalid_otp", "short_desc": "Invalid OTP"}}]})
TICKET = {"responses": [{"message": {"order": {"tags": [{"descriptor": {"code": "grievance-response"}, "list": [
    {"descriptor": {"code": "status"}, "value": "success"},
    {"descriptor": {"code": "ticket-no"}, "value": "T-77"}]}]}}}]}
TICKET_STATUS = {"responses": [{"message": {"catalog": {"providers": [{"items": [{"tags": [
    {"descriptor": {"code": "grievance_status_fetched"}, "list": [
        {"descriptor": {"code": "status", "name": "Status"}, "value": "Open"}]}]}]}]}}}]}


@pytest.fixture
def post(monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap")
    queue = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: queue.pop(0))
    return queue


# --- scheme status -----------------------------------------------------------------------

def test_status_otp_sent_then_verified(post):
    ctx = make_ctx()
    post += [httpx.Response(200, text=""), httpx.Response(200, json=POLICY)]
    pmfby_scheme_status.initiate_pmfby_status_check(ctx, PHONE)
    assert state(ctx) == {"phone_number": PHONE, "status_otp_step": "sent"}

    pmfby_scheme_status.check_pmfby_status_with_otp(ctx, "123456", PHONE, "policy_status", "2025", "Kharif")
    assert state(ctx) == {"phone_number": PHONE, "status_otp_step": "verified", "year": "2025", "season": "Kharif"}
    assert "123456" not in str(ctx.deps.session_state)


def test_status_failures_store_nothing_new(post):
    ctx = make_ctx()
    post += [httpx.Response(503, text="down")]
    pmfby_scheme_status.initiate_pmfby_status_check(ctx, PHONE)
    assert state(ctx) == {}

    post += [httpx.Response(200, text=""), httpx.Response(200, json=NOT_FOUND)]
    pmfby_scheme_status.initiate_pmfby_status_check(ctx, PHONE)
    out = pmfby_scheme_status.check_pmfby_status_with_otp(ctx, "123456", PHONE, "claim_status", "2025", "Rabi")
    assert out.startswith("No claim record found")
    assert state(ctx) == {"phone_number": PHONE, "status_otp_step": "sent"}


# --- grievance ---------------------------------------------------------------------------

def test_grievance_otp_kept_until_submit_then_ticket(post):
    ctx = make_ctx()
    post += [httpx.Response(200, text="{}"), httpx.Response(200, json=OTP_VERIFIED)]
    pmfby_grievance.initiate_pmfby_grievance_otp(ctx, PHONE)
    assert state(ctx) == {"phone_number": PHONE, "grievance_otp_step": "sent"}

    pmfby_grievance.check_pmfby_grievance_otp(ctx, "123456", PHONE)
    assert state(ctx) == {"phone_number": PHONE, "grievance_otp_step": "verified", "grievance_otp": "123456"}

    # Missing application number: verification ran but submit was not sent, so the OTP stays.
    post += [httpx.Response(200, json=OTP_VERIFIED)]
    pmfby_grievance.pmfby_submit_grievance(ctx, "123456", PHONE, "2025", "Kharif", "", "crop loss not paid yet")
    assert state(ctx)["grievance_otp"] == "123456"

    post += [httpx.Response(200, json=OTP_VERIFIED), httpx.Response(200, json=TICKET)]
    out = pmfby_grievance.pmfby_submit_grievance(ctx, "123456", PHONE, "2025", "Kharif", "APP-9", "crop loss not paid yet")
    assert "T-77" in out
    assert state(ctx) == {"phone_number": PHONE, "application_no": "APP-9", "year": "2025", "season": "Kharif",
                          "ticket_no": "T-77"}


def test_grievance_submit_without_ticket_still_clears_otp(post):
    ctx = make_ctx()
    post += [httpx.Response(200, text="{}"), httpx.Response(200, json=OTP_VERIFIED),
             httpx.Response(200, json=OTP_VERIFIED), httpx.Response(500, text="err")]
    pmfby_grievance.initiate_pmfby_grievance_otp(ctx, PHONE)
    pmfby_grievance.check_pmfby_grievance_otp(ctx, "123456", PHONE)
    pmfby_grievance.pmfby_submit_grievance(ctx, "123456", PHONE, "2025", "Kharif", "APP-9", "crop loss not paid yet")
    assert state(ctx) == {"phone_number": PHONE, "grievance_otp_step": "verified"}


def test_grievance_wrong_otp_and_rejected_at_submit(post):
    ctx = make_ctx()
    post += [httpx.Response(200, text="{}"), httpx.Response(200, json=OTP_INVALID)]
    pmfby_grievance.initiate_pmfby_grievance_otp(ctx, PHONE)
    out = pmfby_grievance.check_pmfby_grievance_otp(ctx, "111111", PHONE)
    assert out.startswith("OTP verification failed")
    assert state(ctx) == {"phone_number": PHONE, "grievance_otp_step": "sent"}

    post += [httpx.Response(200, json=OTP_VERIFIED), httpx.Response(200, json=OTP_INVALID)]
    pmfby_grievance.check_pmfby_grievance_otp(ctx, "123456", PHONE)
    pmfby_grievance.pmfby_submit_grievance(ctx, "123456", PHONE, "2025", "Kharif", "APP-9", "crop loss not paid yet")
    assert state(ctx) == {"phone_number": PHONE}


def test_new_otp_drops_stale_otp(post):
    ctx = make_ctx()
    post += [httpx.Response(200, text="{}"), httpx.Response(200, json=OTP_VERIFIED), httpx.Response(200, text="{}")]
    pmfby_grievance.initiate_pmfby_grievance_otp(ctx, PHONE)
    pmfby_grievance.check_pmfby_grievance_otp(ctx, "123456", PHONE)
    pmfby_grievance.initiate_pmfby_grievance_otp(ctx, "9123456780")
    assert state(ctx) == {"phone_number": "9123456780", "grievance_otp_step": "sent"}


def test_grievance_status_records_ticket_only_when_found(post):
    ctx = make_ctx()
    post += [httpx.Response(200, json={"responses": []})]
    pmfby_grievance.pmfby_grievance_status(ctx, PHONE, "T-1")
    assert state(ctx) == {}

    post += [httpx.Response(200, json=TICKET_STATUS)]
    out = pmfby_grievance.pmfby_grievance_status(ctx, PHONE, "T-1")
    assert "Status: Open" in out
    assert state(ctx) == {"phone_number": PHONE, "ticket_no": "T-1"}
