import asyncio
import json
import time
from copy import deepcopy

import httpx
import pytest

from agents.tools.ffs import ffs_get_entitlement, ffs_send_otp, ffs_verify_otp
from app.services import ffs
from tests.test_ffs import setup, context, login, selection


def test_one_village_scope_is_explicit_and_switch_replaces_entitlement(setup):
    for land in setup.backend.land:
        land['villageName'] = 'Shirur'
    third = deepcopy(setup.backend.land[0])
    third.update(farmlandPlotRegistryId=103, farmlandOwnershipId=203,
                 villageLgdCode=561973, villageName='Wadgaon', surveyNumber='3')
    setup.backend.land.append(third)

    async def run():
        view = await login()
        assert view['data']['village_scope'] == {'mode': 'single_village'}
        first = await ffs_get_entitlement(context('first'), 'village-1',
            crop_name='maize', crop_code='0101000')
        assert first['data']['village_scope']['village_name'] == 'Shirur'
        saved = await ffs.get_session('session-1')
        calls = len(setup.backend.calls)
        mixed = await ffs_get_entitlement(context('mixed'), 'village-1', crop_selections=[
            ffs.CropSelection(land_ref=ref, crop_name='maize', crop_code='0101000')
            for ref in ['land-1', 'land-2', 'land-3']])
        assert mixed['status'] == 'needs_clarification'
        assert 'one village at a time' in mixed['next_action']
        assert mixed['data']['village_scope']['village_name'] == 'Shirur'
        assert len(setup.backend.calls) == calls
        assert (await ffs.get_session('session-1'))['selection'] == saved['selection']
        assert (await ffs.get_session('session-1'))['entitlement'] == first
        # Old shop/booking context must not survive a different active selection.
        saved.update(pending_booking={'summary': {'shop_ref': 'old-shop'}},
                     booking_limits={'urea': 1}, booking_tool_exchanges={'old': 'old village'})
        await setup.memory.set(ffs.session_key('session-1'), saved, namespace=ffs.NAMESPACE)
        second = await ffs_get_entitlement(context('switch'), 'village-2',
            crop_name='wheat', crop_code='0101900')
        scope = second['data']['village_scope']
        assert scope == {'mode': 'single_village', 'village_ref': 'village-2',
            'village_name': 'Wadgaon', 'replaces_village': {'village_ref': 'village-1', 'village_name': 'Shirur'}}
        assert second['data']['plots'] == ['land-3']
        assert [p['bags'] for p in second['data']['products']] == [6, 2]  # Never 12/4.
        state = await ffs.get_session('session-1')
        assert state['village_scope'] == scope and state['entitlement'] == second
        assert all(k not in state for k in ('pending_booking', 'booking_limits', 'booking_tool_exchanges'))
        restored = await ffs.prepare_chat_turn('session-1')
        assert restored['village_scope'] == restored['entitlement']['village_scope'] == scope
        gfr = [body for path, body in setup.backend.calls if path.endswith('getRecommendationsByGFRV4')]
        assert [[l['farmlandPlotRegistryId'] for l in body['lands']] for body in gfr] == [[101, 102], [103]]
        assert await ffs_get_entitlement(context('repeat'), 'village-2',
            crop_name='wheat', crop_code='0101900') == second

    asyncio.run(run())


@pytest.mark.parametrize('area_missing', [True, False], ids=['missing', 'null'])
def test_missing_area_does_not_break_login_or_subsequent_chat_turns(setup, area_missing):
    if area_missing:
        setup.backend.land[1].pop('landAreaHectares')
    else:
        setup.backend.land[1]['landAreaHectares'] = None

    async def run():
        await ffs_send_otp(context(), '99999999999', '27')
        view = await ffs_verify_otp(context('verify'), otp='123456')
        # The saved profile is reused on every later chat turn, outside the tool.
        for _ in range(2):
            snapshot = await ffs.prepare_chat_turn('session-1')
            assert snapshot['authenticated'] is True
            assert snapshot['villages'][0]['plots'][1]['area_ha'] is None
        assert view['status'] == 'ok'
        assert view['data']['villages'][0]['plots'][1]['area_ha'] is None
        out = await ffs_get_entitlement(context('all'), 'village-1',
            crop_name='maize', crop_code='0101000')
        assert out['status'] == 'unavailable'
        assert setup.backend.count('/land/crop/add') == 0
        assert setup.backend.count('getRecommendationsByGFRV4') == 0
        # A farmer-selected plot with valid data can still proceed.
        out = await ffs_get_entitlement(context('subset'), 'village-1',
            crop_selections=[ffs.CropSelection(land_ref='land-1', crop_name='maize', crop_code='0101000')],
            selected_plots_only=True)
        assert out['status'] == 'ok' and out['data']['plots'] == ['land-1']
        assert (await ffs.prepare_chat_turn('session-1'))['entitlement']['plots'] == ['land-1']

    asyncio.run(run())


def test_one_crop_expands_whole_village_without_local_status_exclusions(setup):
    setup.backend.land[1].update(disputeStatus='Disputed', claimApprovalStatus='Pending')
    async def run():
        view = await login()
        assert all('restriction' not in p for p in view['data']['villages'][0]['plots'])
        out = await ffs_get_entitlement(context('3'), 'village-1', crop_name='maize', crop_code='0101000')
        assert out['status'] == 'ok'
        writes = [body for path, body in setup.backend.calls if path.endswith('/land/crop/add')]
        assert [w['farmlandPlotRegistryId'] for w in writes] == [101, 102]
        assert out['data']['plots'] == ['land-1', 'land-2']
        assert out['data']['products'][0]['bags'] == 6
        assert 'already_purchased' not in out['data']['products'][0]
        state = await ffs.get_session('session-1')
        assert state['gfr']['recommendations'][0]['alreadyPurchasedBags'] == 1
    asyncio.run(run())


def test_multiple_crops_require_complete_mapping_unless_subset_requested(setup):
    async def run():
        village, crops = selection(await login())
        out = await ffs_get_entitlement(context('3'), village, crops[:1])
        assert out['status'] == 'needs_clarification'
        assert out['data']['missing_plots'][0]['land_ref'] == 'land-2'
        assert setup.backend.count('/land/crop/add') == 0
        out = await ffs_get_entitlement(context('4'), village, crops[:1], selected_plots_only=True)
        assert out['status'] == 'ok' and out['data']['plots'] == ['land-1']
        assert setup.backend.count('/land/crop/add') == 1
    asyncio.run(run())


def test_do_not_mix_village_crop_and_plot_assignments(setup):
    async def run():
        village, crops = selection(await login())
        out = await ffs_get_entitlement(context('3'), village, crops, crop_name='wheat')
        assert out['status'] == 'needs_clarification'
        assert setup.backend.count('/land/crop/add') == 0
    asyncio.run(run())


def test_consistent_repeated_crop_fields_are_accepted_for_subset(setup):
    async def run():
        await login()
        out = await ffs_get_entitlement(context('subset'), 'village-1',
            crop_name='maize', crop_code='0101000', selected_plots_only=True,
            crop_selections=[ffs.CropSelection(land_ref='land-1', crop_name='maize', crop_code='0101000')])
        assert out['status'] == 'ok' and out['data']['plots'] == ['land-1']
    asyncio.run(run())


def test_backend_rejection_is_exposed_and_no_entitlement_is_claimed(setup, monkeypatch):
    original = setup.backend.respond
    async def reject(request):
        if request.url.path.endswith('/land/crop/add'):
            return httpx.Response(400, json={'code': 400, 'status': 'FAILED', 'message': 'FFS cannot accept this plot for this season'})
        return await original(request)
    monkeypatch.setattr(setup.backend, 'respond', reject)
    async def run():
        await login()
        out = await ffs_get_entitlement(context('3'), 'village-1', crop_name='maize', crop_code='0101000')
        assert out['status'] == 'partial_crop_update'
        assert out['data']['provider_error']['message'] == 'FFS cannot accept this plot for this season'
        assert setup.backend.count('getRecommendationsByGFRV4') == 0
        assert (await ffs.get_session('session-1'))['token']
    asyncio.run(run())


def test_required_bags_are_not_used_as_allowed_bags(setup):
    async def run():
        await login()
        del setup.backend.gfr[0]['recommendations'][0]['offeredBags']
        out = await ffs_get_entitlement(context('3'), 'village-1', crop_name='maize', crop_code='0101000')
        assert out['status'] == 'unavailable'
        assert 'offeredBags' in out['next_action']
        assert 'entitlement' not in await ffs.get_session('session-1')
    asyncio.run(run())


def test_old_local_otp_expiry_does_not_block_provider_verification(setup):
    async def run():
        await ffs_send_otp(context(), '99999999999', '27')
        state = setup.memory.values[(ffs.NAMESPACE, ffs.session_key('session-1'))]
        assert 'otp_expires' not in state
        state['otp_expires'] = time.time() - 3600  # Legacy state; no longer authoritative.
        assert (await ffs_verify_otp(context('verify'), otp='123456'))['status'] == 'ok'
        assert setup.backend.count('validateLoginByFarmerId') == 1
    asyncio.run(run())


def test_provider_token_expiry_is_not_capped_at_fifteen_minutes(setup):
    setup.backend.token_lifetime = 3600
    async def run():
        await login()
        assert (await ffs.get_session('session-1'))['token_expires'] > time.time() + 3500
    asyncio.run(run())


def test_transport_failure_does_not_count_as_wrong_otp(setup):
    async def run():
        await ffs_send_otp(context(), '99999999999', '27')
        setup.backend.fail_path = 'validateLoginByFarmerId'
        assert (await ffs_verify_otp(context('verify'), otp='123456'))['status'] == 'unavailable'
        assert (await ffs.get_session('session-1'))['otp_attempts'] == 0
    asyncio.run(run())


def test_ffs_rejection_then_user_subset_resumes_without_login_or_repeated_writes(setup, monkeypatch):
    original = setup.backend.respond
    async def reject_second_plot(request):
        if request.url.path.endswith('/land/crop/add'):
            body = json.loads(request.content)
            if body['farmlandPlotRegistryId'] == 102:
                setup.backend.calls.append((request.url.path, body))
                return httpx.Response(400, json={'code': 400, 'status': 'FAILED',
                    'message': 'Plot 102 is not eligible for this season'})
        return await original(request)
    monkeypatch.setattr(setup.backend, 'respond', reject_second_plot)
    async def run():
        await login()
        full = await ffs_get_entitlement(context('full'), 'village-1', crop_name='maize', crop_code='0101000')
        assert full['status'] == 'partial_crop_update'
        assert full['data']['provider_error']['message'] == 'Plot 102 is not eligible for this season'
        assert full['data']['affected_plot']['land_ref'] == 'land-2'
        assert full['data']['affected_plot']['survey'] == '2'
        assert setup.backend.count('getRecommendationsByGFRV4') == 0
        assert setup.backend.count('/land/crop/add') == 2
        # FFS now returns only the remaining plot. The farmer explicitly chooses it.
        setup.backend.land = setup.backend.land[:1]
        subset = await ffs_get_entitlement(context('subset'), 'village-1',
            crop_selections=[ffs.CropSelection(land_ref='land-1', crop_name='maize')],
            selected_plots_only=True)
        assert subset['status'] == 'ok' and subset['data']['plots'] == ['land-1']
        assert setup.backend.count('loginByFarmerId') == 1
        assert setup.backend.count('validateLoginByFarmerId') == 1
        assert setup.backend.count('/land/crop/add') == 2  # No retry of either plot's write.
        request = next(body for path, body in setup.backend.calls if path.endswith('getRecommendationsByGFRV4'))
        assert [row['farmlandPlotRegistryId'] for row in request['lands']] == [101]
    asyncio.run(run())
