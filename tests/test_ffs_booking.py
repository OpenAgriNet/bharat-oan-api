"""Booking contracts and failure recovery; synthetic HTTP only, no provider writes."""

import asyncio
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
        ffs.BookingQuantity(product=name, bags=bags) for name, bags in [("urea", urea), ("DAP", dap)] if bags
    ]


async def prepare():
    village, crops = selection(await login())
    await ffs_get_entitlement(context("3"), village, crops)
    return (await ffs_find_shops(context("4")))["data"]["shops"][0]["shop_ref"]


async def preview(shop, turn="5", q=None):
    return await ffs_create_booking(context(turn), shop, quantities=q or quantities())


async def confirm(shop, view, turn="6", **kwargs):
    return await ffs_create_booking(context(turn), shop, True, view["data"]["confirmation_token"], **kwargs)


@pytest.mark.parametrize("bags", [0, -1, True, 1.5, "2"])
def test_quantity_schema_rejects_invalid_counts(bags):
    with pytest.raises(ValidationError):
        ffs.BookingQuantity(product="urea", bags=bags)


def test_farmer_quantity_preview_later_confirmation_and_cached_completion(setup):
    async def run():
        shop = await prepare()
        state = await ffs.get_session("session-1")
        assert state["user"]["userMobileNumber"] == "9999999999"
        missing = await ffs_create_booking(context("5"), shop)
        assert missing["status"] == "needs_quantities"
        view = await preview(shop)
        assert view["data"]["summary"]["products"][0]["bags"] == 2
        assert (await confirm(shop, view, "5"))["status"] == "needs_confirmation"
        assert not setup.backend.booking_payloads
        out = await confirm(shop, view)
        assert out["status"] == "ok"
        assert await confirm(shop, view, "7") == out
        assert len(setup.backend.booking_payloads) == 1
        p = setup.backend.booking_payloads[0]
        assert p["contactNoForCommunication"] == p["receiverMobileNo"] == "9999999999"
        assert len(p["selectedLandIds"]) == 2
        assert p["bookingRequest"][0]["productLines"][0]["noBagsRequested"] == 2
        assert p["bookingRequest"][0]["productLines"][0]["totalQuantityKgRequested"] == 90

    asyncio.run(run())


@pytest.mark.parametrize("changed", ["quantity", "crop", "expired", "token"])
def test_changed_or_expired_preview_needs_fresh_confirmation(setup, changed):
    async def run():
        shop = await prepare()
        view = await preview(shop)
        kw = {}
        if changed == "quantity":
            kw["quantities"] = quantities(1)
        elif changed == "token":
            view["data"]["confirmation_token"] = "wrong"
        else:
            state = await ffs.get_session("session-1")
            if changed == "crop":
                state["selection"][0]["crop_code"] = "0101900"
            else:
                state["pending_booking"]["expires"] = 0
            await setup.memory.set(ffs.session_key("session-1"), state, namespace=ffs.NAMESPACE)
        fresh = await confirm(shop, view, **kw)
        assert fresh["status"] == "needs_confirmation"
        assert not setup.backend.booking_payloads
        assert (await confirm(shop, fresh, "6"))["status"] == "needs_confirmation"
        assert (await confirm(shop, fresh, "7"))["status"] == "ok"

    asyncio.run(run())


def test_partial_shop_books_only_requested_supported_products(setup):
    setup.backend.stock = {i: (20, 0) for i in range(8)}

    async def run():
        shop = await prepare()
        wrong = await preview(shop, q=quantities(2, 1))
        assert wrong["status"] == "needs_quantities"
        view = await preview(shop)
        assert [p["name"] for p in view["data"]["summary"]["products"]] == ["urea"]
        assert (await confirm(shop, view))["status"] == "ok"
        assert [
            p["productId"] for p in setup.backend.booking_payloads[0]["bookingRequest"][0]["productLines"]
        ] == [1089]

    asyncio.run(run())


@pytest.mark.parametrize("http_status", [200, 500])
def test_provider_new_limit_requires_new_quantity_and_confirmation(setup, http_status):
    setup.backend.booking_error = fixture("over_limit.json")
    setup.backend.booking_http_status = http_status

    async def run():
        shop = await prepare()
        view = await preview(shop)
        rejected = await confirm(shop, view)
        assert rejected["status"] == "quantity_limit_changed"
        assert rejected["data"] == dict(
            product="urea", requested_bags=2, allowed_bags=1, already_purchased_bags=0
        )
        assert (await confirm(shop, view))["status"] == "already_started"
        assert len(setup.backend.booking_payloads) == 1
        assert (await preview(shop, "7"))["status"] == "needs_quantities"
        fresh = await preview(shop, "7", quantities(1))
        assert fresh["status"] == "needs_confirmation"
        assert (await confirm(shop, fresh, "7"))["status"] == "needs_confirmation"
        setup.backend.booking_error = None
        assert (await confirm(shop, fresh, "8"))["status"] == "ok"
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
        assert (await confirm(shop, v))["data"]["allowed_bags"] == 0
        assert (await preview(shop, "7", quantities(1)))["status"] == "needs_quantities"
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "unknown_error", "malformed", "inconsistent"])
def test_uncertain_outcome_blocks_later_turns(setup, failure):
    if failure == "timeout":
        setup.backend.booking_timeout = True
    elif failure == "unknown_error":
        setup.backend.booking_error = {
            "code": 500,
            "data": None,
            "status": "FAILED",
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
        assert (await preview(shop, "8"))["status"] == "booking_uncertain"
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


def test_concurrent_confirmations_send_once(setup):
    async def run():
        shop = await prepare()
        v = await preview(shop)
        answers = await asyncio.gather(confirm(shop, v, "6"), confirm(shop, v, "7"))
        assert all(a["status"] == "ok" for a in answers)
        assert len(setup.backend.booking_payloads) == 1

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
        assert (await ffs_verify_otp(context("2", otp="123456")))["status"] == "ok"
        s = await ffs.get_session("session-1")
        # Already established selections: test booking consumes them without changing crops.
        lands = s["profile"]["landData"][:2]
        service = ffs.FFSService("session-1", "test", "3")
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
        shop = (await ffs_find_shops(context("4")))["data"]["shops"][0]["shop_ref"]
        view = await preview(shop, q=quantities(1))
        out = await confirm(shop, view)
        assert out["status"] == "ok"  # Nested statusCode=304 is a business code, not HTTP failure.
        assert out["data"]["booking"]["bookings"][0]["expires_at"] == "2026-11-06 23:00:00"
        p = setup.backend.booking_payloads[0]
        assert p["receiverUserId"] == responses["/validateLoginByFarmerId"]["data"]["userId"]
        assert [l["cropCode"] for l in p["selectedLandIds"]] == ["0702300", "0101900"]
        assert len(p["selectedLandIds"]) == 2

    asyncio.run(run())


def test_real_pydantic_booking_schema_and_two_user_turns(setup):
    async def run():
        shop = await prepare()
        token = None
        step = 0

        def model(messages, info):
            nonlocal token, step
            step += 1
            if step == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "ffs_create_booking",
                            {"shop_ref": shop, "quantities": [{"product": "urea", "bags": 2}]},
                        )
                    ]
                )
            if step == 2:
                token = messages[-1].parts[0].content["data"]["confirmation_token"]
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "ffs_create_booking",
                            {"shop_ref": shop, "confirmed": True, "confirmation_token": token},
                        )
                    ]
                )
            if step == 3:
                assert messages[-1].parts[0].content["status"] == "needs_confirmation"
                return ModelResponse(parts=[TextPart("Confirm two bags?")])
            if step == 4:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "ffs_create_booking",
                            {"shop_ref": shop, "confirmed": True, "confirmation_token": token},
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("Booking confirmed.")])

        agent = Agent(FunctionModel(model), tools=[Tool(ffs_create_booking, takes_ctx=True, sequential=True)])
        first = await agent.run("I want two urea bags", deps=context("5").deps)
        assert not setup.backend.booking_payloads
        await agent.run("Yes, confirm", deps=context("6").deps, message_history=first.all_messages())
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
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


def test_same_farmer_relogin_does_not_reset_uncertain_booking(setup):
    setup.backend.booking_timeout = True

    async def run():
        shop = await prepare()
        view = await preview(shop)
        assert (await confirm(shop, view))["status"] == "booking_uncertain"
        sent = await ffs_send_otp(context("7", query="resend OTP"), "99999999999", "27", True)
        assert sent["status"] == "otp_sent"
        assert (await ffs_create_booking(context("8"), shop))["status"] == "booking_uncertain"
        assert len(setup.backend.booking_payloads) == 1

    asyncio.run(run())


def test_duplicate_products_and_above_known_limit_never_submit(setup):
    async def run():
        shop = await prepare()
        for q in [quantities(7), quantities() + quantities()]:
            assert (await preview(shop, q=q))["status"] == "needs_quantities"
        assert not setup.backend.booking_payloads

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
        assert (await preview(shop))["status"] == "needs_shops"
        refreshed = await ffs_find_shops(context("6"))
        shop = refreshed["data"]["shops"][0]["shop_ref"]
        view = await preview(shop, "7")
        assert (await confirm(shop, view, "8"))["status"] == "ok"

    asyncio.run(run())
