"""FFS owns active/parallel bookings; our tools must return the current attempt."""
import asyncio
import json

import httpx
import pytest

from app.services import ffs
from agents.tools.ffs import ffs_create_booking, ffs_find_shops
from tests.test_ffs import setup, context
from tests.test_ffs_booking import prepare, quantities


def test_new_dap_order_after_urea_success_returns_provider_rejection_not_old_receipt(setup):
    async def run():
        shop = await prepare(quantities(1))
        first = await ffs_create_booking(context('urea'), shop, True)
        assert first['status'] == 'ok'
        setup.backend.booking_error = {'code': 500, 'status': 'FAILED', 'data': None,
                                       'message': 'An active booking already exists'}
        searched = await ffs_find_shops(context('dap-search'), order=quantities(0, 1), shop_ref=shop)
        assert 'booking' not in searched['data']
        second = await ffs_create_booking(context('dap-book'), shop, True)
        assert second['status'] == 'booking_failed'
        assert second['data']['provider_error']['message'] == 'An active booking already exists'
        assert second['data']['booking']['bookings'] == []
        assert 'PRC-TEST-001' not in json.dumps(second)
        assert [[p['productId'] for p in b['bookingRequest'][0]['productLines']]
                for b in setup.backend.booking_payloads] == [[1089], [1005]]
    asyncio.run(run())


@pytest.mark.parametrize('accepted_before_timeout', [False, True])
def test_lost_response_can_retry_and_ffs_decides_if_booking_exists(setup, monkeypatch, accepted_before_timeout):
    original = setup.backend.respond
    attempts = []
    async def provider(request):
        if not request.url.path.endswith('createMultipleBookingV2'):
            return await original(request)
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            if accepted_before_timeout:
                await original(request)
            raise httpx.ReadTimeout('Lost response', request=request)
        if accepted_before_timeout:
            return httpx.Response(500, json={'code': 500, 'status': 'FAILED', 'data': None,
                'message': 'A booking is already active or being processed'})
        return await original(request)
    monkeypatch.setattr(setup.backend, 'respond', provider)
    async def run():
        shop = await prepare(quantities(1))
        first = await ffs_create_booking(context('first'), shop, True)
        assert first['status'] == 'booking_uncertain'
        assert 'may be retried' in first['next_action']
        assert len(attempts) == 1  # No HTTP retry loop inside the tool.
        assert 'uncertain_booking' not in await ffs.get_session('session-1')
        second = await ffs_create_booking(context('retry'), shop, True)
        assert second['status'] == ('booking_failed' if accepted_before_timeout else 'ok')
        assert len(attempts) == 2
        assert attempts[0]['bookingRequest'] == attempts[1]['bookingRequest']
        if accepted_before_timeout:
            assert second['data']['booking']['bookings'] == []
    asyncio.run(run())


def test_legacy_booking_markers_do_not_block_or_replay(setup):
    async def run():
        shop = await prepare(quantities(1))
        state = await ffs.get_session('session-1')
        state['uncertain_booking'] = {'shop_ref': shop}
        state['last_booking_result'] = {'status': 'ok', 'data': {'booking_code': 'OLD'}}
        await setup.memory.set(ffs.session_key('session-1'), state, namespace=ffs.NAMESPACE)
        assert 'last_booking_result' not in await ffs.prepare_chat_turn('session-1')
        result = await ffs_create_booking(context('fresh'), shop, True)
        assert result['status'] == 'ok'
        assert len(setup.backend.booking_payloads) == 1
        assert 'OLD' not in json.dumps(result)
        state = await ffs.get_session('session-1')
        assert 'last_booking_result' not in state and 'uncertain_booking' not in state
    asyncio.run(run())


def test_empty_order_correction_cannot_submit_old_order(setup):
    async def run():
        shop = await prepare(quantities(2))
        assert (await ffs_find_shops(context('empty'), order=[]))['status'] == 'needs_order'
        assert (await ffs_create_booking(context('book'), shop, True))['status'] == 'needs_order'
        assert not setup.backend.booking_payloads
    asyncio.run(run())


@pytest.mark.parametrize('action', ['more', 'widen'])
def test_repeated_order_preserves_paging_even_with_selected_shop(setup, action):
    async def run():
        shop = await prepare(quantities(0, 1))
        state = await ffs.get_session('session-1')
        first = {s['shop_ref'] for s in state['shops']['last_result']['data']['shops']}
        await ffs_create_booking(context('preview'), shop, False)
        answer = await ffs_find_shops(context('next'), action=action, order=quantities(0, 1))
        assert answer['status'] == 'ok'
        assert answer['data']['shops']
        assert not first.intersection(s['shop_ref'] for s in answer['data']['shops'])
        assert 'shop_retained' not in answer['data']
        if action == 'widen':
            assert all(s['territory'] == 'D' for s in answer['data']['shops'])
        state = await ffs.get_session('session-1')
        assert [(p['product_id'], p['bags']) for p in state['order']] == [(1005, 1)]
    asyncio.run(run())


def test_changed_order_restarts_search_even_when_more_was_sent(setup):
    async def run():
        await prepare(quantities(1))
        state = await ffs.get_session('session-1')
        first = [s['shop_ref'] for s in state['shops']['last_result']['data']['shops']]
        before = setup.backend.count('getRetailersByProductAvailabilityV4')
        answer = await ffs_find_shops(context('changed'), action='more', order=quantities(2))
        assert [s['shop_ref'] for s in answer['data']['shops']] == first
        assert setup.backend.count('getRetailersByProductAvailabilityV4') > before
        assert (await ffs.get_session('session-1'))['order'][0]['bags'] == 2
    asyncio.run(run())
