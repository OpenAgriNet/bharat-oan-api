"""Provider decisions must not be replaced by unused calls or local guesses."""
import asyncio
import json

import httpx
import pytest

from agents.tools.ffs import ffs_create_booking, ffs_send_otp, ffs_verify_otp
from app.services import ffs
from tests.test_ffs import setup, context
from tests.test_ffs_booking import prepare, quantities


def test_unused_dashboard_quota_is_not_requested_or_saved(setup):
    setup.backend.fail_path = 'getApplicantDashboardQuota'
    async def run():
        await prepare()
        assert setup.backend.count('getApplicantDashboardQuota') == 0
        assert setup.backend.count('getRecommendationsByGFRV4') == 1
        assert 'quota' not in await ffs.get_session('session-1')
    asyncio.run(run())


@pytest.mark.parametrize('mobile', ['+91 99999 99999', '919999999999', '1234567890', None])
def test_booking_passes_provider_contact_unchanged_and_reports_provider_rejection(setup, monkeypatch, mobile):
    original = setup.backend.respond
    requests = []
    async def respond(request):
        if request.url.path.endswith('createMultipleBookingV2'):
            requests.append(json.loads(request.content))
            return httpx.Response(400, json={'code': 400, 'status': 'FAILED', 'data': None,
                                           'message': 'Provider contact validation failed'})
        return await original(request)
    monkeypatch.setattr(setup.backend, 'respond', respond)
    async def run():
        shop = await prepare()
        state = await ffs.get_session('session-1')
        state['user']['userMobileNumber'] = mobile
        state['user']['userFullName'] = None
        await setup.memory.set(ffs.session_key('session-1'), state, namespace=ffs.NAMESPACE)
        answer = await ffs_create_booking(context('book'), shop, True)
        assert answer['status'] == 'booking_failed'
        assert answer['data']['provider_error']['message'] == 'Provider contact validation failed'
        assert len(requests) == 1
        assert requests[0]['contactNoForCommunication'] == mobile
        assert requests[0]['receiverMobileNo'] == mobile
        assert requests[0]['receiverName'] is None
    asyncio.run(run())


def test_booking_does_not_reapply_cached_gfr_limit(setup):
    async def run():
        shop = await prepare(quantities(1))
        state = await ffs.get_session('session-1')
        state['gfr']['recommendations'][0]['offeredBags'] = 0
        await setup.memory.set(ffs.session_key('session-1'), state, namespace=ffs.NAMESPACE)
        setup.backend.booking_error = {'code': 500, 'status': 'FAILED', 'data': None,
                                       'message': 'Provider says no remaining allowance'}
        answer = await ffs_create_booking(context('book'), shop, True)
        assert answer['status'] == 'booking_failed'
        assert len(setup.backend.booking_payloads) == 1
        assert answer['data']['provider_error']['message'] == setup.backend.booking_error['message']
    asyncio.run(run())


@pytest.mark.parametrize('message', ['No data found for given Aadhaar', 'कोई डेटा उपलब्ध नहीं है'])
def test_missing_profile_is_not_classified_by_english_text(setup, monkeypatch, message):
    original = setup.backend.respond
    async def respond(request):
        if request.url.path.endswith('applicantFetch'):
            return httpx.Response(200, json={'code': 200, 'data': None, 'message': message})
        return await original(request)
    monkeypatch.setattr(setup.backend, 'respond', respond)
    async def run():
        await ffs_send_otp(context(), '99999999999', '27')
        answer = await ffs_verify_otp(context('verify'), otp='123456')
        assert answer['status'] == 'setup_incomplete'
        assert answer['data']['provider_error']['message'] == message
        state = await ffs.get_session('session-1')
        assert 'profile' not in state
        assert state['token']
    asyncio.run(run())


def test_explicit_empty_lands_still_returns_no_lands(setup):
    setup.backend.land = []
    async def run():
        await ffs_send_otp(context(), '99999999999', '27')
        answer = await ffs_verify_otp(context('verify'), otp='123456')
        assert answer['status'] == 'no_lands'
    asyncio.run(run())
