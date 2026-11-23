"""Session state written by the PM-KISAN status and grievance tools (section "pmkisan")."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agents.deps import FarmerContext
from agents.tools import pmkisan_grievance, pmkisan_scheme_status

CTX = {"action": "on_x", "timestamp": "t", "message_id": "m", "transaction_id": "tx",
       "domain": "schemes:vistaar", "version": "1.1.0"}


def make_ctx():
    return SimpleNamespace(deps=FarmerContext(session_id="s", query="q"))


def state(ctx):
    return ctx.deps.session_state.get("pmkisan", {})


def init_ok():
    order = {"provider": {"id": "pmkisan"}, "items": [{"id": "pmkisan", "tags": [
        {"descriptor": {"code": "otp", "short_desc": "OTP sent"}}]}]}
    return {"context": CTX, "responses": [{"context": CTX, "message": {"order": order}}]}


def status(state_code):
    order = {"fulfillments": [{"state": {"descriptor": {"code": state_code, "short_desc": state_code}}}]}
    return {"context": CTX, "responses": [{"context": CTX, "message": {"order": order}}]}


@pytest.fixture
def sync_post(monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap")
    queue = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: queue.pop(0))
    return queue


@pytest.fixture
def async_post(monkeypatch):
    monkeypatch.setattr(pmkisan_grievance, "BAP_ENDPOINT", "http://bap")
    queue = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            return queue.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return queue


# --- status flow -----------------------------------------------------------------------

def test_status_otp_sent_then_verified_by_reg_no(sync_post):
    ctx = make_ctx()
    sync_post += [httpx.Response(200, json=init_ok()), httpx.Response(200, json=status("COMPLETED"))]

    pmkisan_scheme_status.initiate_pm_kisan_status_check(ctx, reg_no="tn 110700729")
    assert state(ctx) == {"reg_no": "TN110700729", "status_otp_step": "sent"}

    pmkisan_scheme_status.check_pm_kisan_status_with_otp(ctx, otp="1234", reg_no="TN110700729")
    assert state(ctx) == {"reg_no": "TN110700729", "status_otp_step": "verified"}
    assert "1234" not in str(ctx.deps.session_state)


def test_status_by_phone_and_failures_store_nothing_new(sync_post):
    ctx = make_ctx()
    sync_post += [httpx.Response(500, text="down")]
    pmkisan_scheme_status.initiate_pm_kisan_status_check(ctx, phone_number="9876543210")
    assert state(ctx) == {}

    sync_post += [httpx.Response(200, json=init_ok()), httpx.Response(200, json=status("FAILED"))]
    pmkisan_scheme_status.initiate_pm_kisan_status_check(ctx, phone_number="9876543210")
    pmkisan_scheme_status.check_pm_kisan_status_with_otp(ctx, otp="9999", phone_number="9876543210")
    assert state(ctx) == {"phone_number": "9876543210", "status_otp_step": "sent"}


# --- grievance flow ----------------------------------------------------------------------

def test_grievance_status_otp_sent_then_verified(async_post):
    ctx = make_ctx()
    search = {"responses": [{"message": {"order": {"tags": [{"list": [
        {"descriptor": {"code": "grievance-status"}, "value": "Pending"}]}]}}}]}
    async_post += [httpx.Response(200, json=init_ok()), httpx.Response(200, json=status("COMPLETED")),
                   httpx.Response(200, json=search)]

    asyncio.run(pmkisan_grievance.pmkisan_grievance_send_otp(ctx, "tn110700729", purpose="check_status"))
    assert state(ctx) == {"reg_no": "TN110700729", "grievance_otp_step": "sent"}

    asyncio.run(pmkisan_grievance.pmkisan_grievance_status(ctx, reg_no="TN110700729", otp="1234"))
    # The OTP is used once (verify + search in the same call), so it is not stored.
    assert state(ctx) == {"reg_no": "TN110700729", "grievance_otp_step": "verified"}


def test_grievance_wrong_otp_keeps_sent(async_post):
    ctx = make_ctx()
    async_post += [httpx.Response(200, json=init_ok()), httpx.Response(200, json=status("FAILED"))]
    asyncio.run(pmkisan_grievance.pmkisan_grievance_send_otp(ctx, "TN110700729", purpose="check_status"))
    out = asyncio.run(pmkisan_grievance.pmkisan_grievance_status(ctx, reg_no="TN110700729", otp="0000"))
    assert "not successful" in out
    assert state(ctx) == {"reg_no": "TN110700729", "grievance_otp_step": "sent"}


def test_grievance_submit_kill_switch_stores_nothing(async_post):
    ctx = make_ctx()
    out = asyncio.run(pmkisan_grievance.pmkisan_grievance_send_otp(ctx, "TN110700729"))
    assert out == pmkisan_grievance._PMKISAN_GRIEVANCE_SUBMIT_DISABLED_MSG
    asyncio.run(pmkisan_grievance.pmkisan_submit_grievance(
        ctx, "TN110700729", "money not received yet", pmkisan_grievance.GRIEVANCE_TYPES[0], otp="1234"))
    assert state(ctx) == {}


def test_grievance_submit_request_records_grievance_no(async_post):
    """Submit is behind a kill switch; its request helper still records the number for when it returns."""
    ctx = make_ctx()
    ok = {"responses": [{"message": {"order": {"tags": [{"list": [
        {"descriptor": {"code": "grievance-id"}, "value": "PMK123"}]}]}}}]}
    async_post += [httpx.Response(200, json=ok), httpx.Response(200, json={"error": {"code": "x"}})]
    gtype = pmkisan_grievance.GRIEVANCE_TYPES[0]
    asyncio.run(pmkisan_grievance._submit_grievance_init_request(ctx, "TN110700729", gtype, "money not received", "", False))
    assert state(ctx) == {"grievance_no": "PMK123"}

    ctx2 = make_ctx()
    asyncio.run(pmkisan_grievance._submit_grievance_init_request(ctx2, "TN110700729", gtype, "money not received", "", False))
    assert state(ctx2) == {}
