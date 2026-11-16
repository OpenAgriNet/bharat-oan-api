import asyncio
import csv
import io
import json
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from agents import ffs_crop_resolver as resolver
from agents.tools.ffs import ffs_get_entitlement
from app.services import ffs
from tests.test_ffs import setup, context, login, selection


def specialist(monkeypatch, output, seen):
    def model(messages, info):
        seen.extend(messages)
        return ModelResponse(parts=[TextPart(json.dumps({"matches": output}))])
    agent = Agent(FunctionModel(model), output_type=PromptedOutput(resolver.CropMatches), retries=0)
    monkeypatch.setattr(resolver, 'crop_agent', lambda: agent)
    monkeypatch.setattr(resolver, 'get_registry', lambda: SimpleNamespace(
        get_timeout=lambda _: 1, get_default_alias=lambda _: 'test'))


def test_isolated_batch_passes_only_names_codes_and_sorts_results(monkeypatch):
    seen = []
    specialist(monkeypatch, [{'index': 1, 'status': 'matched', 'crop_code': '002'},
                            {'index': 0, 'status': 'matched', 'crop_code': '001'}], seen)
    answer = asyncio.run(resolver.resolve_crops(['makka', 'wheat'], [
        {'cropCode': '001', 'cropName': 'मका', 'unused': 'omit me'},
        {'cropCode': '002', 'cropName': 'गहू, wheat'}], state_code='27', session_id='private-session', question_id='private-qid'))
    assert [m.crop_code for m in answer] == ['001', '002']
    prompts = [part.content for message in seen for part in message.parts if part.part_kind == 'user-prompt']
    assert len(prompts) == 1
    assert prompts[0].startswith('State LGD code: 27\n')
    catalogue, phrases = prompts[0].split('Crop catalogue (CSV):\n')[1].split('\nFarmer phrases (JSON):\n')
    assert list(csv.DictReader(io.StringIO(catalogue))) == [
        {'crop_name': 'मका', 'crop_code': '001'}, {'crop_name': 'गहू, wheat', 'crop_code': '002'}]
    assert json.loads(phrases) == [{'index': 0, 'text': 'makka'}, {'index': 1, 'text': 'wheat'}]
    assert 'omit me' not in prompts[0]
    assert 'private-session' not in prompts[0] and 'private-qid' not in prompts[0]


@pytest.mark.parametrize('matches', [
    [{'index': 0, 'status': 'matched', 'crop_code': 'invented'}],
    [{'index': 0, 'status': 'matched', 'crop_code': None}],
    [{'index': 0, 'status': 'ambiguous', 'candidates': ['invented']}],
    [{'index': 0, 'status': 'ambiguous', 'candidates': []}],
    [{'index': 0, 'status': 'not_found', 'crop_code': '001'}],
    [],
    [{'index': 1, 'status': 'matched', 'crop_code': '001'}],
    [{'index': 0, 'status': 'matched', 'crop_code': '001'}] * 2,
])
def test_invalid_specialist_result_is_rejected(monkeypatch, matches):
    specialist(monkeypatch, matches, [])
    with pytest.raises(ValueError):
        asyncio.run(resolver.resolve_crops(['crop'], [{'cropCode': '001', 'cropName': 'Crop'}],
                                          state_code='27', session_id='s', question_id='q'))


def test_ambiguity_blocks_all_writes_then_reuses_matches_on_clarification(setup, monkeypatch):
    calls = []
    async def resolve(names, crops, **kwargs):
        calls.append(names)
        return [resolver.CropMatch(index=i, status='ambiguous', candidates=['0101000', '0101900'])
                if name == 'unclear' else resolver.CropMatch(index=i, status='matched', crop_code='0101000')
                for i, name in enumerate(names)]
    monkeypatch.setattr(ffs, 'resolve_crops', resolve)
    async def run():
        village, choices = selection(await login())
        choices[1].crop_name = 'unclear'
        out = await ffs_get_entitlement(context('3'), village, choices)
        assert out['status'] == 'needs_clarification'
        assert out['data']['crops'][0]['candidates'][0]['crop_name'] == 'मका'
        assert setup.backend.count('/land/crop/add') == 0
        choices[1].crop_name = 'corn'
        out = await ffs_get_entitlement(context('4'), village, choices)
        assert out['status'] == 'ok'
        assert calls == [['maize', 'unclear'], ['corn']]
        assert setup.backend.count('/land/crop/add') == 2
        await ffs_get_entitlement(context('5'), village, choices)
        assert len(calls) == 2
        assert 'catalogue' not in json.dumps(out)
    asyncio.run(run())


def test_model_timeout_does_not_write_or_restart_login(setup, monkeypatch):
    async def timeout(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(ffs, 'resolve_crops', timeout)
    async def run():
        village, crops = selection(await login())
        out = await ffs_get_entitlement(context('3'), village, crops)
        assert out['status'] == 'unavailable'
        assert setup.backend.count('/land/crop/add') == 0
        assert (await ffs.get_session('session-1'))['token']
    asyncio.run(run())


def test_state_catalog_shared_across_farmers_and_excluded_from_farmer_view(setup):
    async def run():
        first = await login('one')
        await login('two')
        assert setup.backend.count('getCropListByState') == 1
        assert setup.backend.count('/bharatVistar/applicantFetch') == 2
        assert 'current_crops' not in json.dumps(first)
        for session in ['one', 'two']:
            state = await ffs.get_session(session)
            assert all(set(row) == {'cropCode', 'cropName'} for row in state['crops'])
    asyncio.run(run())


def test_provider_null_candidates_is_an_empty_list(monkeypatch):
    specialist(monkeypatch, [{'index': 0, 'status': 'matched', 'crop_code': '001', 'candidates': None}], [])
    answer = asyncio.run(resolver.resolve_crops(['crop'], [{'cropCode': '001', 'cropName': 'Crop'}],
                                              state_code='27', session_id='s', question_id='q'))
    assert answer[0].candidates == []


def test_resolver_timeout_is_bounded(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(resolver, 'crop_agent', lambda: SimpleNamespace(run=slow))
    monkeypatch.setattr(resolver, 'get_registry', lambda: SimpleNamespace(
        get_timeout=lambda _: 0.01, get_default_alias=lambda _: 'test'))
    with pytest.raises(TimeoutError):
        asyncio.run(resolver.resolve_crops(['crop'], [], state_code='27', session_id='s', question_id='q'))


@pytest.mark.parametrize('code,wording', [('0101900', 'गेहूं'), ('0101000', 'makkaa'), ('0101700', 'धान')])
def test_common_crop_codes_bypass_specialist(setup, monkeypatch, code, wording):
    setup.backend.crops.append({'cropCode': '0101700', 'cropName': 'Rice'})
    async def never(*args, **kwargs):
        pytest.fail('Common code must not invoke a specialist')
    monkeypatch.setattr(ffs, 'resolve_crops', never)
    async def run():
        view = await login()
        assert {row['crop_code'] for row in view['data']['common_crops']} == set(ffs.COMMON_CROPS)
        snapshot = await ffs.prepare_chat_turn('session-1')
        assert snapshot['common_crops'] == view['data']['common_crops']
        village, choices = selection(view, wording)
        for choice in choices:
            choice.crop_code = code
        out = await ffs_get_entitlement(context('3'), village, choices)
        assert out['status'] == 'ok'
        writes = [body for path, body in setup.backend.calls if path.endswith('/land/crop/add')]
        assert len(writes) == 2 and all(body['cropCode'] == code for body in writes)
    asyncio.run(run())


@pytest.mark.parametrize('code', ['0101700', '0801200', 'invented'])
def test_unadvertised_codes_cannot_bypass_specialist(setup, code):
    async def run():
        view = await login()
        assert code not in {row['crop_code'] for row in view['data']['common_crops']}
        village, choices = selection(view)
        choices[0].crop_code = code
        assert (await ffs_get_entitlement(context('3'), village, choices))['status'] == 'needs_clarification'
        assert setup.backend.count('/land/crop/add') == 0
    asyncio.run(run())


def test_mixed_selection_only_resolves_other_crop(setup, monkeypatch):
    calls = []
    async def resolve(names, crops, **kwargs):
        calls.append(names)
        return [resolver.CropMatch(index=0, status='matched', crop_code='0801200')]
    monkeypatch.setattr(ffs, 'resolve_crops', resolve)
    async def run():
        village, choices = selection(await login())
        choices[0].crop_code = '0101000'
        choices[1].crop_name = 'soybean'
        assert (await ffs_get_entitlement(context('3'), village, choices))['status'] == 'ok'
        assert calls == [['soybean']]
    asyncio.run(run())
