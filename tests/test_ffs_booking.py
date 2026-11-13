"""Booking contracts and failure recovery; synthetic HTTP only, no provider writes."""

import asyncio
import ast
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, Tool
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.services import ffs
from agents.tools.ffs import (
    ffs_create_booking,
    ffs_get_entitlement,
    ffs_find_shops,
    ffs_send_otp,
    ffs_verify_otp,
)
from tests.test_ffs import setup, context, login, selection, validate_booking_payload

FIXTURES = Path(__file__).parent / "fixtures/ffs_v2"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def quantities(urea=2, dap=0):
    return [
        ffs.OrderItem(product=name, product_id=1089 if name == "urea" else 1005, bags=bags) for name, bags in [("urea", urea), ("DAP", dap)] if bags
    ]


async def prepare(q=None):
    village, crops = selection(await login())
    await ffs_get_entitlement(context("3"), village, crops)
    return (await ffs_find_shops(context("4"), order=q or quantities()))["data"]["shops"][0]["shop_ref"]


async def preview(shop, turn="5", q=None):
    if q is not None:
        answer = await ffs_find_shops(context(turn + "-order"), order=q)
        if answer["status"] != "ok":
            return answer
    return await ffs_create_booking(context(turn), shop)


async def confirm(shop, view, turn="6", **kwargs):
    return await ffs_create_booking(context(turn), shop, True, **kwargs)


def test_confirmation_restores_compact_order_without_raw_exchanges(setup):
    async def run():
        shop = await prepare()
        view = await preview(shop)
        restored = await ffs.prepare_chat_turn('session-1')
        assert 'booking_tool_exchanges' not in restored
        assert 'backend_exchanges' not in json.dumps(restored)
        assert 'pending_booking' not in restored
        assert restored['selected_shop']['shop_ref'] == shop
        assert restored['order'][0]['bags'] == 2
        state = await ffs.get_session('session-1')
        assert 'booking_tool_exchanges' not in state
        assert 'pending_booking' not in state
        result = await confirm(shop, view)
        assert result['status'] == 'ok'
        assert 'backend_exchanges' not in json.dumps(result)
        assert result['data']['booking']['bookings'][0]['booking_code'] == 'PRC-TEST-001'
        completed = await ffs.prepare_chat_turn('session-1')
        assert 'booking_tool_exchanges' not in completed
        assert 'last_booking_result' not in completed
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())


def test_optional_summary_never_creates_token_or_expiry(setup):
    async def run():
        shop = await prepare()
        first = await preview(shop, '5')
        repeated = await preview(shop, '6')
        assert first == repeated
        state = await ffs.get_session('session-1')
        assert 'pending_booking' not in state
        assert not setup.backend.booking_payloads
        assert (await confirm(shop, repeated, '6'))['status'] == 'ok'
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())


def test_repeated_confirmations_without_token_get_fresh_provider_outcomes(setup):
    """Replay the observed confirmation sequence with the removed token argument omitted."""
    for i in range(2, 5):
        setup.backend.land.append(dict(setup.backend.land[0],
            farmlandPlotRegistryId=101 + i, farmlandOwnershipId=201 + i,
            ownershipMappingId=301 + i, surveyNumber=str(i + 1)))
    async def run():
        shop = await prepare()
        view = await preview(shop, 'preview-turn', quantities(1))
        assert view['status'] == 'needs_confirmation'
        attempts = [
            {'confirmed': True},
            {'confirmed': True},
            {'confirmed': True},
        ]
        answers = []
        for index, arguments in enumerate(attempts):
            # Each tool invocation reloads persisted state, just like different workers.
            answers.append(await ffs_create_booking(
                context(f'yes-{index}', query='Yes confirm my booking'), shop, **arguments))
            setup.backend.booking_error = {'code': 500, 'status': 'FAILED', 'data': None, 'message': 'An active booking already exists'}
        assert [a['status'] for a in answers] == ['ok', 'booking_failed', 'booking_failed']
        assert len(setup.backend.booking_payloads) == 3
        assert len(setup.backend.booking_payloads[0]['selectedLandIds']) == 5
        line = setup.backend.booking_payloads[0]['bookingRequest'][0]['productLines'][0]
        assert line['noBagsRequested'] == 1
        assert line['quantityOfBagKg'] == 45

    asyncio.run(run())


@pytest.mark.parametrize('prior_state', ['none', 'same_turn', 'legacy_expired'])
def test_confirmed_choice_submits_without_local_preview_gate(setup, prior_state):
    async def run():
        shop = await prepare(quantities(1))
        if prior_state != 'none':
            await preview(shop, '6', quantities(1))
        if prior_state == 'legacy_expired':
            state = await ffs.get_session('session-1')
            state['pending_booking'] = dict(confirmation_token='old-unused-token',
                signature='old-unused-signature', expires=0, turn='6')
            await setup.memory.set(ffs.session_key('session-1'), state, namespace=ffs.NAMESPACE)
        answer = await ffs_create_booking(context('6', query='Yes, book one bag here'),
            shop, confirmed=True)
        assert answer['status'] == 'ok'
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())


@pytest.mark.parametrize("bags", [0, -1, True, 1.5, "1.5", "one"])
def test_quantity_schema_rejects_invalid_counts(bags):
    with pytest.raises(ValidationError):
        ffs.OrderItem(product="urea", product_id=1089, bags=bags)


def test_farmer_quantity_single_confirmation_and_provider_duplicate_rejection(setup):
    async def run():
        shop = await prepare()
        state = await ffs.get_session("session-1")
        assert state["user"]["userMobileNumber"] == "9999999999"
        missing = await ffs_create_booking(context("5"), shop)
        assert missing["status"] == "needs_confirmation"
        view = await preview(shop)
        assert view["data"]["summary"]["products"][0]["bags"] == 2
        assert not setup.backend.booking_payloads
        out = await confirm(shop, view)
        assert out["status"] == "ok"
        setup.backend.booking_error = {"code": 500, "status": "FAILED", "data": None, "message": "Active booking exists"}
        assert (await confirm(shop, view, "7"))["status"] == "booking_failed"
        assert len(setup.backend.booking_payloads) == 2
        p = setup.backend.booking_payloads[0]
        assert p["contactNoForCommunication"] == p["receiverMobileNo"] == "9999999999"
        assert len(p["selectedLandIds"]) == 2
        assert p["bookingRequest"][0]["productLines"][0]["noBagsRequested"] == 2
        assert p["bookingRequest"][0]["productLines"][0]["totalQuantityKgRequested"] == 90

    asyncio.run(run())


def test_farmer_corrected_quantity_submits_without_another_confirmation(setup):
    async def run():
        shop = await prepare()
        await preview(shop, '5', quantities(2))
        await ffs_find_shops(context('6-order'), order=quantities(1))
        answer = await ffs_create_booking(context('6', query='Make it one bag and book'),
            shop, confirmed=True)
        assert answer['status'] == 'ok'
        assert setup.backend.booking_payloads[0]['bookingRequest'][0]['productLines'][0]['noBagsRequested'] == 1
    asyncio.run(run())


def test_partial_shop_books_only_requested_supported_products(setup):
    setup.backend.stock = {i: (20, 0) for i in range(8)}

    async def run():
        shop = await prepare()
        wrong = await preview(shop, q=quantities(2, 1))
        assert wrong["status"] == "needs_confirmation"
        view = await preview(shop, q=quantities())
        assert [p["name"] for p in view["data"]["summary"]["products"]] == ["Neem Coated Urea"]
        assert (await confirm(shop, view))["status"] == "ok"
        assert [
            p["productId"] for p in setup.backend.booking_payloads[0]["bookingRequest"][0]["productLines"]
        ] == [1089]

    asyncio.run(run())


@pytest.mark.parametrize("http_status", [200, 500])
def test_provider_new_limit_then_farmer_correction_submits_directly(setup, http_status):
    setup.backend.booking_error = fixture("over_limit.json")
    setup.backend.booking_http_status = http_status

    async def run():
        shop = await prepare()
        view = await preview(shop)
        rejected = await confirm(shop, view)
        assert rejected["status"] == "booking_failed"
        assert rejected["data"]["provider_error"]["message"] == setup.backend.booking_error["message"]
        assert 'allowed_bags' not in rejected['data']  # No English-text extraction.
        assert not (await ffs.get_session('session-1')).get('uncertain_booking')
        assert 'booking_tool_exchanges' not in await ffs.get_session('session-1')
        assert rejected['data']['provider_error']['http_status'] == http_status
        assert setup.backend.booking_payloads[0]['bookingRequest'][0]['productLines'][0]['noBagsRequested'] == 2
        assert len(setup.backend.booking_payloads) == 1
        setup.backend.booking_error = None
        await ffs_find_shops(context('7-order'), order=quantities(1))
        corrected = await ffs_create_booking(context('7', query='Then book one bag'),
            shop, confirmed=True)
        assert corrected['status'] == 'ok'
        assert [
            p["bookingRequest"][0]["productLines"][0]["noBagsRequested"]
            for p in setup.backend.booking_payloads
        ] == [2, 1]

    asyncio.run(run())


def test_zero_limit_no_automatic_retry(setup):
    setup.backend.booking_error = fixture("over_limit.json")
    setup.backend.booking_error["message"] = setup.backend.booking_error["message"].replace(
        "Allowed now: 1", "Allowed now: 0"
    )

    async def run():
        shop = await prepare()
        v = await preview(shop)
        rejected = await confirm(shop, v)
        assert rejected["status"] == "booking_failed"
        assert "Allowed now: 0" in rejected["data"]["provider_error"]["message"]
        assert 'allowed_bags' not in rejected['data']
        assert not (await ffs.get_session('session-1')).get('uncertain_booking')
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "unknown_error", "malformed", "inconsistent"])
def test_uncertain_outcome_does_not_block_later_attempts(setup, failure):
    if failure == "timeout":
        setup.backend.booking_timeout = True
    elif failure == "unknown_error":
        setup.backend.booking_error = {
            "code": 500,
            "data": None,
            "status": "UNKNOWN",
            "message": "Unknown provider issue",
        }
    elif failure == "malformed":
        setup.backend.booking_data_override = {}
    else:
        setup.backend.booking_data_override = {"isValid": True, "bookingResults": []}

    async def run():
        shop = await prepare()
        v = await preview(shop)
        assert (await confirm(shop, v))["status"] == "booking_uncertain"
        assert (await confirm(shop, v, "7"))["status"] == "booking_uncertain"
        assert (await preview(shop, "8"))["status"] == "needs_confirmation"
        assert len(setup.backend.booking_payloads) == 2

    asyncio.run(run())


def test_session_lock_serializes_confirmations_without_replaying_success(setup):
    async def run():
        shop = await prepare()
        v = await preview(shop)
        answers = await asyncio.gather(
            ffs_create_booking(context('6'), shop, True),
            ffs_create_booking(context('7'), shop, True))
        assert all(a["status"] == "ok" for a in answers)
        assert len(setup.backend.booking_payloads) == 2  # This fixture allows both; Python adds no booking rule.

    asyncio.run(run())


def test_v2_captured_shapes_with_multiplot_contract(setup, monkeypatch):
    original = setup.backend.respond
    responses = {
        "/validateLoginByFarmerId": fixture("step2_response.json"),
        "/bharatVistar/applicantFetch": fixture("step5_response.json"),
        "/getRecommendationsByGFRV4": fixture("step9_response.json"),
        "/getProductsByAvailability": fixture("step10_response.json"),
        "/getRetailersByProductAvailabilityV4": fixture("step11_response.json"),
        "/createMultipleBookingV2": fixture("step12_response.json"),
    }
    # Keep the real captured envelope and fields; align the synthetic request identity.
    responses["/createMultipleBookingV2"]["data"]["bookingResults"][0]["retailerId"] = responses[
        "/getRetailersByProductAvailabilityV4"
    ]["data"][0]["retailerId"]

    async def respond(request):
        for suffix, response in responses.items():
            if request.url.path.endswith(suffix):
                if suffix == "/createMultipleBookingV2":
                    body = json.loads(request.content)
                    validate_booking_payload(body)
                    setup.backend.booking_payloads.append(body)
                return httpx.Response(200, json=response)
        return await original(request)

    # setup installs an HTTP MockTransport with the original bound method; replace client factory.
    class Client(setup.client_class):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    async def run():
        assert (await ffs_send_otp(context(), "99999999999", "24"))["status"] == "otp_sent"
        assert (await ffs_verify_otp(context("2"), otp="123456"))["status"] == "ok"
        s = await ffs.get_session("session-1")
        # Already established selections: test booking consumes them without changing crops.
        lands = s["profile"]["landData"][:2]
        service = ffs.FFSService("session-1", "3")
        service.state = s
        mapping = service.land_map()
        refs = [ref for ref, l in mapping.items() if l in lands]
        s["selection"] = [
            {"land_ref": ref, "crop_code": crop} for ref, crop in zip(refs, ["0702300", "0101900"])
        ]
        s["gfr"] = responses["/getRecommendationsByGFRV4"]["data"][0]
        s["entitlement"] = ffs.result(
            "ok",
            "Choose quantity",
            products=[{"group": 11, "name": "urea", "bags": 1, "kg_per_bag": 45, "already_purchased": 0}],
        )
        s["stage"] = "entitlement_ready"
        await setup.memory.set(ffs.session_key("session-1"), s, namespace=ffs.NAMESPACE)
        shop = (await ffs_find_shops(context("4"), order=quantities(1)))["data"]["shops"][0]["shop_ref"]
        view = await preview(shop, q=quantities(1))
        out = await confirm(shop, view)
        assert out["status"] == "ok"  # Nested statusCode=304 is a business code, not HTTP failure.
        assert out["data"]["booking"]["bookings"][0]["expires_at"] == "2026-11-06 23:00:00"
        p = setup.backend.booking_payloads[0]
        assert p["receiverUserId"] == responses["/validateLoginByFarmerId"]["data"]["userId"]
        assert [l["cropCode"] for l in p["selectedLandIds"]] == ["0702300", "0101900"]
        assert len(p["selectedLandIds"]) == 2

    asyncio.run(run())


def test_real_pydantic_tool_submits_user_confirmed_shop_without_preview(setup):
    async def run():
        shop = await prepare()
        def model(messages, info):
            schema = next(t.parameters_json_schema for t in info.function_tools if t.name == 'ffs_create_booking')
            assert 'confirmation_token' not in schema['properties']
            assert 'quantities' not in schema['properties']
            if messages[-1].parts[0].part_kind == 'tool-return':
                response = messages[-1].parts[0].content
                assert response['status'] == 'ok'
                assert response['data']['booking']['is_valid'] is True
                assert 'backend_exchanges' not in json.dumps(response)
                return ModelResponse(parts=[TextPart('Booking created.')])
            return ModelResponse(parts=[ToolCallPart('ffs_create_booking', {
                'shop_ref': shop, 'confirmed': True,
            })])
        agent = Agent(FunctionModel(model), tools=[Tool(ffs_create_booking, takes_ctx=True, sequential=True)])
        await agent.run('Yes, book one bag from that shop', deps=context('6').deps)
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["waitlisted", "rejected"])
def test_individual_booking_outcomes(setup, mode):
    data = fixture("step12_response.json")["data"]
    row = data["bookingResults"][0]
    row["retailerId"] = 0
    if mode == "waitlisted":
        row["isWaitlisted"] = True
    else:
        data["isValid"] = row["isValid"] = False
        data["totalBookingsSuccessful"] = 0
        data["totalBookingsFailed"] = 1
        row["procurementId"] = None
        row["procurementApplicationCode"] = None
    setup.backend.booking_data_override = data

    async def run():
        shop = await prepare()
        view = await preview(shop)
        state = await ffs.get_session("session-1")
        row["retailerId"] = next(
            s["retailer_id"] for s in state["shops"]["pool"].values() if s["shop_ref"] == shop
        )
        result = await confirm(shop, view)
        assert result["status"] == ("ok" if mode == "waitlisted" else "booking_failed")
        if mode == "waitlisted":
            assert result["data"]["booking"]["bookings"][0]["is_waitlisted"] is True
        assert "uncertain_booking" not in await ffs.get_session("session-1")

    asyncio.run(run())


def test_save_failure_after_provider_success_is_uncertain(setup, monkeypatch):
    from redis.exceptions import RedisError

    original = setup.memory.set

    async def save(key, value, **kwargs):
        if isinstance(value, dict) and value.get("stage") == "booking_done":
            raise RedisError("Synthetic save failure")
        await original(key, value, **kwargs)

    async def run():
        shop = await prepare()
        view = await preview(shop)
        monkeypatch.setattr(setup.memory, "set", save)
        assert (await confirm(shop, view))["status"] == "booking_uncertain"
        assert (await confirm(shop, view, "7"))["status"] == "booking_uncertain"
        assert len(setup.backend.booking_payloads) == 2

    asyncio.run(run())


def test_relogin_has_no_old_booking_block_but_requires_setup_and_order(setup):
    setup.backend.booking_timeout = True

    async def run():
        shop = await prepare()
        view = await preview(shop)
        assert (await confirm(shop, view))["status"] == "booking_uncertain"
        sent = await ffs_send_otp(context("7", query="resend OTP"), "99999999999", "27", True)
        assert sent["status"] == "otp_sent"
        assert (await ffs_create_booking(context("8"), shop))["status"] == "needs_otp"
        assert "uncertain_booking" not in await ffs.get_session("session-1")
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


def test_duplicate_product_lines_require_valid_request_shape(setup):
    async def run():
        shop = await prepare()
        assert (await preview(shop, q=quantities() + quantities()))['status'] == 'needs_order'
        assert not setup.backend.booking_payloads
    asyncio.run(run())


def test_order_above_offered_bags_is_not_dispatched_or_silently_capped(setup):
    async def run():
        shop = await prepare()
        answer = await ffs_find_shops(context('6'), order=quantities(7))
        assert answer['status'] == 'needs_order'
        assert answer['data']['allowed_bags'] == 6
        assert not setup.backend.booking_payloads
        assert not (await ffs.get_session('session-1')).get('order')
        assert (await ffs_create_booking(context('7'), shop, True))['status'] == 'needs_order'
    asyncio.run(run())


def test_strict_mock_rejects_missing_contract_fields(setup):
    async def run():
        shop = await prepare()
        v = await preview(shop)
        await confirm(shop, v)
        body = setup.backend.booking_payloads[0]
        for key in ("receiverMobileNo", "receiverAadharHash", "isReceiverApplicantSame"):
            broken = deepcopy(body)
            del broken[key]
            with pytest.raises(AssertionError):
                validate_booking_payload(broken)
        broken = deepcopy(body)
        del broken["bookingRequest"][0]["productLines"][0]["totalQuantityKgRequested"]
        with pytest.raises(AssertionError):
            validate_booking_payload(broken)

    asyncio.run(run())


def test_old_shop_cache_can_be_refreshed_for_booking(setup):
    async def run():
        shop = await prepare()
        state = await ffs.get_session("session-1")
        for row in state["shops"]["pool"].values():
            row.pop("stock")
        await setup.memory.set(ffs.session_key("session-1"), state, namespace=ffs.NAMESPACE)
        assert (await preview(shop))["status"] == "needs_confirmation"
        refreshed = await ffs_find_shops(context("6"))
        shop = refreshed["data"]["shops"][0]["shop_ref"]
        view = await preview(shop, "7")
        assert (await confirm(shop, view, "8"))["status"] == "ok"

    asyncio.run(run())
