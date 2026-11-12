"""Structural outcomes and retailer identity during farmer-directed corrections."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.services import ffs
from agents.tools.ffs import ffs_create_booking, ffs_find_shops
from helpers.ffs_model_view import compact_ffs_result
from tests.test_ffs import setup, context
from tests.test_ffs_booking import prepare, preview, confirm, quantities


@pytest.mark.parametrize("message", ["Not available; choose a smaller amount.", "अनुरोध अस्वीकार किया गया।", "731 bags in stock; this farmer may request 1 bag."])
@pytest.mark.parametrize("status", [200, 500])
def test_failed_null_is_definite_in_any_language_with_original_reason(setup, message, status):
    setup.backend.booking_error = {"code": 500, "status": "FAILED", "data": None, "message": message}
    setup.backend.booking_http_status = status
    async def run():
        shop = await prepare()
        answer = await ffs_create_booking(context("book"), shop, True)
        assert answer["status"] == "booking_failed"
        assert answer["data"]["provider_error"] == {"http_status": status, **setup.backend.booking_error}
        assert compact_ffs_result(answer) == answer
        state = await ffs.get_session("session-1")
        assert "uncertain_booking" not in state
        assert not state.get("booking_limits")
        assert "last_booking_result" not in await ffs.prepare_chat_turn("session-1")
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())


def test_structured_rejection_preserves_product_numbers_and_message(setup):
    setup.backend.booking_data_override = {"isValid": False, "totalBookingsSuccessful": 0,
        "message": "Choose fewer bags", "productId": 1089, "productGroupId": 11,
        "allowedBags": 1, "alreadyPurchasedBags": 4}
    async def run():
        shop = await prepare()
        answer = await ffs_create_booking(context("book"), shop, True)
        assert answer["status"] == "booking_failed"
        assert answer["data"]["provider_error"]["data"] == setup.backend.booking_data_override
        assert not (await ffs.get_session("session-1")).get("uncertain_booking")
    asyncio.run(run())


@pytest.mark.parametrize("data", [
    {"isValid": False, "totalBookingsSuccessful": 1},
    {"isValid": False, "totalBookingsSuccessful": 0, "procurementId": 123},
    {"isValid": False, "totalBookingsSuccessful": 0, "totalBookingsFailed": 0},
    {"isValid": False, "totalBookingsSuccessful": 0, "bookingResults": [{"retailerId": 1, "isValid": True, "procurementId": 123}]},
    {"isValid": True, "totalBookingsSuccessful": 0},
])
def test_contradictory_results_stay_uncertain(setup, data):
    setup.backend.booking_data_override = data
    async def run():
        shop = await prepare()
        answer = await ffs_create_booking(context("book"), shop, True)
        assert answer["status"] == "booking_uncertain"
        assert (await ffs_create_booking(context("again"), shop, True))["status"] == "booking_uncertain"
        assert len(setup.backend.booking_payloads) == 2
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["non_json", "connection", "missing_data", "null_with_success"])
def test_unreadable_envelope_has_no_internal_retry_or_later_block(setup, monkeypatch, failure):
    original = setup.backend.respond
    attempts = []
    async def respond(request):
        if request.url.path.endswith("createMultipleBookingV2"):
            attempts.append(request)
            if failure == "connection":
                raise httpx.ConnectError("connection lost", request=request)
            if failure == "non_json":
                return httpx.Response(502, text="not JSON")
            if failure == "missing_data":
                return httpx.Response(500, json={"code": 500, "status": "FAILED"})
            return httpx.Response(500, json={"code": 500, "status": "FAILED", "data": None, "totalBookingsSuccessful": 1})
        return await original(request)
    monkeypatch.setattr(setup.backend, "respond", respond)
    async def run():
        shop = await prepare()
        assert (await ffs_create_booking(context("book"), shop, True))["status"] == "booking_uncertain"
        assert (await ffs_create_booking(context("retry"), shop, True))["status"] == "booking_uncertain"
        assert len(attempts) == 2
    asyncio.run(run())


def test_correction_retains_selected_retailer_without_rediscovery(setup):
    setup.backend.booking_error = {"code": 500, "status": "FAILED", "data": None, "message": "Allowed now: 1 bag"}
    async def run():
        shop = await prepare()
        assert (await ffs_create_booking(context("book"), shop, True))["status"] == "booking_failed"
        before = setup.backend.count("getRetailersByProductAvailabilityV4")
        setup.backend.booking_error = None
        # A different ranking would make a different shop first if we re-searched.
        setup.backend.stock = {i: (1000 if i == 0 else 1, 0) for i in range(8)}
        updated = await ffs_find_shops(context("correct"), order=quantities(1), shop_ref=shop)
        assert 'shop_retained' not in updated["data"]
        assert updated["data"]["selected_shop"]["shop_ref"] == shop
        assert setup.backend.count("getRetailersByProductAvailabilityV4") == before
        assert (await ffs_create_booking(context("correct-book"), shop, True))["status"] == "ok"
        bookings = [b["bookingRequest"][0] for b in setup.backend.booking_payloads]
        assert bookings[0]["retailerId"] == bookings[1]["retailerId"]
        assert [b["productLines"][0]["noBagsRequested"] for b in bookings] == [2, 1]
    asyncio.run(run())


def test_insufficient_cached_stock_does_not_block_corrected_booking(setup):
    setup.backend.stock = {0: (1, 0), 1: (2, 0), 2: (3, 0), 3: (4, 0), 4: (5, 0), 5: (6, 0)}
    async def run():
        selected_ref = await prepare(quantities(1))  # Village Shop 1 has two bags.
        await preview(selected_ref)
        original = (await ffs.get_session("session-1"))["selected_shop"]["retailer_id"]
        updated = await ffs_find_shops(context("correct"), order=quantities(3))
        assert 'shop_retained' not in updated["data"]
        assert updated["data"]["selected_shop"]["shop_ref"] == selected_ref
        assert 'supply' not in updated["data"]["selected_shop"]
        assert not setup.backend.booking_payloads
        state = await ffs.get_session("session-1")
        assert state["selected_shop"]["retailer_id"] == original
        # Provider, not cached stock, decides whether the correction is accepted.
        setup.backend.booking_error = {"code": 500, "status": "FAILED", "data": None, "message": "Not enough stock"}
        answer = await ffs_create_booking(context("correct-book"), selected_ref, True)
        assert answer['status'] == 'booking_failed'
        assert answer['data']['provider_error']['message'] == 'Not enough stock'
        assert setup.backend.booking_payloads[0]["bookingRequest"][0]["retailerId"] == original
        assert setup.backend.booking_payloads[0]["bookingRequest"][0]['productLines'][0]['noBagsRequested'] == 3
    asyncio.run(run())


def test_stable_reference_does_not_depend_on_result_position(setup):
    async def run():
        service = ffs.FFSService("session", "turn")
        service.state = {"order": [{"product_id": 1089, "name": "urea", "bags": 1}]}
        row = {"retailerId": 1538627, "retailerName": "chosen", "availableProducts": []}
        search = {"level": 0, "pool": {}}
        service._add_retailer(search, row)
        ref = search["pool"]["1538627"]["shop_ref"]
        second = {"level": 1, "pool": {}}
        service._add_retailer(second, dict(row, retailerId=999))
        service._add_retailer(second, row)
        assert second["pool"]["1538627"]["shop_ref"] == ref
        assert ref != second["pool"]["999"]["shop_ref"]
    asyncio.run(run())


def test_numeric_string_bags_are_not_language_interpretation():
    assert ffs.OrderItem(product="urea", product_id=1089, bags="1").bags == 1


def test_old_shop_retention_result_does_not_restore_removed_stock_gate():
    old = {'status': 'ok', 'next_action': 'Chosen shop has insufficient stock; choose another',
           'data': {'shops': [], 'shop_retained': False,
                    'selected_shop': {'shop_ref': 'retailer-1', 'name': 'Shop 1',
                                      'territory': 'V', 'supply': 'partial', 'stock': {'urea': 2}}}}
    clean = compact_ffs_result(old)
    assert 'shop_retained' not in clean['data']
    assert set(clean['data']['selected_shop']) == {'shop_ref', 'name', 'territory'}
    assert 'choose another' not in clean['next_action']
    assert old['data']['shop_retained'] is False


def test_definite_auth_rejection_keeps_existing_login_expiry_behavior(setup):
    setup.backend.booking_error = {"code": 401, "status": "FAILED", "data": None, "message": "Login expired"}
    setup.backend.booking_http_status = 401
    async def run():
        shop = await prepare()
        assert (await ffs_create_booking(context("book"), shop, True))["status"] == "booking_failed"
        state = await ffs.get_session("session-1")
        assert not state.get("token") and state["stage"] == "login_expired"
        assert "uncertain_booking" not in state
        assert (await ffs_create_booking(context("again"), shop, True))["status"] == "needs_otp"
        assert len(setup.backend.booking_payloads) == 1
    asyncio.run(run())
