"""Model-facing login arguments and provider-side effects under session-ID storage."""
import asyncio
import time

import httpx
from pydantic_ai import Tool
import pytest

from agents.tools.ffs import ffs_send_otp, ffs_verify_otp
from app.services import ffs
from tests.test_ffs import setup, context, login


def test_model_schema_has_lgd_list_and_explicit_otp_string():
    send = Tool(ffs_send_otp, takes_ctx=True).tool_def
    verify = Tool(ffs_verify_otp, takes_ctx=True).tool_def
    assert set(send.parameters_json_schema["properties"]) == {"farmer_id", "state_lgd_code", "resend"}
    assert "state_lgd_code" in send.parameters_json_schema["required"]
    assert send.parameters_json_schema["properties"]["state_lgd_code"]["type"] == "string"
    for code, name in ffs.STATE_CODES.items():
        assert f"{name}: {code}" in send.description
    assert "Gujarat: 24" in send.description and "Maharashtra: 27" in send.description
    otp_schema = verify.parameters_json_schema["properties"]["otp"]
    assert {s["type"] for s in otp_schema["anyOf"]} == {"string", "null"}
    assert not verify.parameters_json_schema.get("required")  # Resume authenticated setup.


@pytest.mark.parametrize("state", ["Gujrat", "Gujarat", "Ahmedabad", "ગુજરાત", "99", ""])
def test_python_does_not_interpret_state_names_or_accept_unknown_codes(setup, state):
    answer = asyncio.run(ffs_send_otp(context(), "99999999999", state))
    assert answer["status"] == "needs_clarification"
    assert setup.backend.calls == []


def test_selected_lgd_and_normalized_farmer_id_reach_encrypted_provider_body(setup):
    answer = asyncio.run(ffs_send_otp(context(), "९९९९९-९९९ ९९९", "२४"))
    assert answer["status"] == "otp_sent"
    assert setup.backend.calls[0][1] == {"userStateLgdCode": "24", "userFarmerId": "99999999999"}


@pytest.mark.parametrize("otp", ["001234", "००१२३४", "00 12 34", "00-12-34"])
def test_leading_zero_and_formatted_otp_reach_provider_once(setup, otp):
    setup.backend.expected_otp = "001234"
    async def run():
        await ffs_send_otp(context(), "99999999999", "27")
        answer = await ffs_verify_otp(context("verify"), otp=otp)
        assert answer["status"] == "ok"
        assert await ffs_verify_otp(context("verify"), otp=otp) == answer
        assert setup.backend.count("validateLoginByFarmerId") == 1
        payload = next(body for path, body in setup.backend.calls if path.endswith("validateLoginByFarmerId"))
        assert payload["otp"] == "001234"
    asyncio.run(run())


@pytest.mark.parametrize("otp", [None, "", "12345", "1234567", "double zero one two", "123a56"])
def test_incomplete_or_uninterpreted_otp_does_not_call_provider(setup, otp):
    async def run():
        await ffs_send_otp(context(), "99999999999", "27")
        assert (await ffs_verify_otp(context("verify"), otp=otp))["status"] == "needs_otp"
        assert setup.backend.count("validateLoginByFarmerId") == 0
        assert (await ffs.get_session("session-1"))["otp_attempts"] == 0
    asyncio.run(run())


def test_same_turn_rejected_otp_is_not_retried_with_another_guess(setup):
    async def run():
        await ffs_send_otp(context(), "99999999999", "27")
        assert (await ffs_verify_otp(context("verify"), otp="000000"))["status"] == "invalid_otp"
        assert (await ffs_verify_otp(context("verify"), otp="123456"))["status"] == "invalid_otp"
        assert setup.backend.count("validateLoginByFarmerId") == 1
    asyncio.run(run())


def test_resend_uses_structured_intent_not_words_in_query(setup):
    async def run():
        await ffs_send_otp(context(), "99999999999", "27")
        no = await ffs_send_otp(context("no", query="don't resend"), "99999999999", "27", resend=False)
        assert no["status"] == "already_started"
        yes = await ffs_send_otp(context("yes", query="send another code"), "99999999999", "27", resend=True)
        assert yes["status"] == "otp_sent"
        assert setup.backend.count("loginByFarmerId") == 2
    asyncio.run(run())


@pytest.mark.parametrize('expiry', ['login_stage', 'token_expiry', 'otp_http_400', 'otp_body_400'])
def test_new_otp_after_expiry_does_not_require_resend_flag(setup, monkeypatch, expiry):
    original = setup.backend.respond

    async def expired_otp(request):
        if request.url.path.endswith('validateLoginByFarmerId'):
            return httpx.Response(400 if expiry == 'otp_http_400' else 200,
                json={'code': 400, 'status': 'FAILED', 'message': 'OTP expired'})
        return await original(request)

    async def run():
        if expiry.startswith('otp_'):
            monkeypatch.setattr(setup.backend, 'respond', expired_otp)
            await ffs_send_otp(context(), '99999999999', '27')
            out = await ffs_verify_otp(context('verify'), otp='123456')
            assert out['data']['provider_error']['message'] == 'OTP expired'
        else:
            await login()
            state = setup.memory.values[(ffs.NAMESPACE, ffs.session_key('session-1'))]
            if expiry == 'login_stage':
                state.pop('token')
                state['stage'] = 'login_expired'
            else:
                state['token_expires'] = time.time() - 1
        assert setup.backend.count('loginByFarmerId') == 1  # No automatic SMS.
        out = await ffs_send_otp(context('send-again'), '99999999999', '27')
        assert out['status'] == 'otp_sent'
        assert setup.backend.count('loginByFarmerId') == 2
        assert (await ffs_send_otp(context('send-again'), '99999999999', '27'))['status'] == 'already_started'
        assert (await ffs_send_otp(context('later'), '99999999999', '27'))['status'] == 'already_started'
        assert setup.backend.count('loginByFarmerId') == 2

    asyncio.run(run())


def test_legacy_owner_metadata_does_not_block_context_or_setup_resume(setup):
    async def run():
        await login()
        state = setup.memory.values[(ffs.NAMESPACE, ffs.session_key("session-1"))]
        state["owner"] = "legacy-specific-token-hash"
        setup.memory.values[("ffs-owner", ffs.session_key("session-1"))] = "legacy-specific-token-hash"
        before = len(setup.backend.calls)
        assert (await ffs.prepare_chat_turn("session-1"))["authenticated"] is True
        assert (await ffs_verify_otp(context("resume")))["status"] == "ok"
        assert len(setup.backend.calls) == before
        # Legacy keys expire normally; this code never refreshes or reads their binding.
        assert await ffs.prepare_chat_turn("other-session") == {}
        assert (await ffs_verify_otp(context("other", session="other-session"), otp="123456"))["status"] == "needs_otp"
    asyncio.run(run())
