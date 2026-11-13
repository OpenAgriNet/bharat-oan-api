"""Real Langfuse spans exported to memory; all FFS/model traffic stays mocked."""
import asyncio
import importlib
import json
import logging
import uuid

import httpx
import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agents.tools.ffs import ffs_send_otp, ffs_get_entitlement, ffs_find_shops, ffs_create_booking
from app.services import ffs
from helpers import ffs_tracing
from tests.test_ffs import setup, context, login, selection, KEY, IV


@pytest.fixture
def tracing(monkeypatch):
    monkeypatch.setenv('OTEL_SDK_DISABLED', 'false')
    monkeypatch.setenv('LANGFUSE_TRACING_ENABLED', 'true')
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    def no_network(request):
        raise AssertionError('Unexpected telemetry network request')
    client = Langfuse(public_key='pk-lf-test-' + uuid.uuid4().hex, secret_key='sk-lf-test',
                      base_url='http://unused.invalid', tracing_enabled=True,
                      tracer_provider=provider, span_exporter=exporter,
                      httpx_client=httpx.Client(transport=httpx.MockTransport(no_network)))
    monkeypatch.setattr(ffs_tracing, 'get_client', lambda: client)
    yield client, exporter
    client.flush()
    provider.shutdown()


def summary_logs(caplog):
    return '\n'.join(r.getMessage() for r in caplog.records if r.name == 'helpers.ffs_tracing')


def output(span):
    return json.loads(span.attributes['langfuse.observation.output'])


def test_console_diagnostics_remain_visible_when_info_is_disabled(caplog):
    caplog.set_level(logging.WARNING, logger='helpers.ffs_tracing')
    ffs_tracing._emit(logging.INFO, {'event': 'test', 'status': 'ok'})
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert json.loads(caplog.records[0].getMessage().removeprefix('ffs '))['status'] == 'ok'


def test_real_span_tree_safe_outputs_and_cached_decisions(setup, tracing, caplog):
    client, exporter = tracing
    caplog.set_level(logging.INFO, logger='helpers.ffs_tracing')
    async def run():
        with client.start_as_current_observation(name='test.agent'):
            village, crops = selection(await login())
            answer = await ffs_get_entitlement(context('3'), village, crops)
            assert answer['status'] == 'ok'
            assert await ffs_get_entitlement(context('4'), village, crops) == answer
            await ffs_find_shops(context('5'), order=[ffs.OrderItem(product='urea', product_id=1089, bags=1)])
    asyncio.run(run())
    client.flush()
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == 'test.agent')
    tools = [s for s in spans if s.name.startswith('tool:ffs_')]
    apis = [s for s in spans if s.name.startswith('ffs.api:')]
    assert len(tools) == 5 and len(apis) >= 10
    assert all(s.context.trace_id == root.context.trace_id for s in spans)
    assert all(s.parent.span_id == root.context.span_id for s in tools)
    assert all(s.parent.span_id in {t.context.span_id for t in tools} for s in apis)
    cached = next(s for s in tools if output(s).get('api_calls') == 0)
    assert {'event': 'entitlement_reused'} in output(cached)['decisions']
    assert {'event': 'crop_resolution_reused', 'count': 1} in output(cached)['decisions']
    quantity = next(s for s in tools if any(d['event'] == 'entitlement_quantity' for d in output(s)['decisions']))
    assert output(quantity)['products'][0]['bags'] == 6
    assert output(quantity)['stage_after'] == 'entitlement_ready'
    assert all(output(s)['duration_ms'] >= 0 for s in tools + apis)
    serialized = json.dumps([dict(s.attributes) for s in spans]) + summary_logs(caplog)
    for secret in ['123456', '99999999999', '9999999999', 'test-hash', 'Mock Farmer', KEY, IV, '.signature']:
        assert secret not in serialized
    assert '_state' not in serialized and 'Authorization' not in serialized
    assert 'trace_id' in caplog.text and 'question_id' in caplog.text


@pytest.mark.parametrize('status,body,reason', [
    (401, {'message': 'reCAPTCHA token missing secret-value', 'code': 401}, 'captcha_rejected'),
    (401, {'message': 'secret-value', 'code': 401}, 'unauthorized'),
    (200, {'message': 'secret-value', 'code': 500}, 'provider_rejected'),
    (503, 'secret-value', 'non_json_response'),
])
def test_api_failures_keep_safe_provider_reason(setup, tracing, caplog, status, body, reason):
    client, exporter = tracing
    caplog.set_level(logging.INFO, logger='helpers.ffs_tracing')
    async def response(request):
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    service = ffs.FFSService('session-test', 'question-test')
    async def run():
        async with setup.client_class(transport=httpx.MockTransport(response)) as http:
            service.client = http
            with pytest.raises(ffs.FFSError):
                await service.call('POST', ffs.USER, '/authenticate/user/loginByFarmerId',
                                   {'userFarmerId': 'secret-value'}, encrypted=True)
    asyncio.run(run())
    client.flush()
    span = exporter.get_finished_spans()[-1]
    assert output(span)['http_status'] == status
    assert output(span)['provider_reason'] == reason
    assert span.attributes['langfuse.observation.level'] == 'ERROR'
    assert 'secret-value' not in json.dumps(dict(span.attributes)) + summary_logs(caplog)


def test_timeout_keeps_error_class_without_sensitive_message(setup, tracing, caplog):
    client, exporter = tracing
    setup.backend.fail_path = '/loginByFarmerId'
    answer = asyncio.run(ffs_send_otp(context(), '99999999999', '27'))
    assert answer['status'] == 'unavailable'
    client.flush()
    api = next(s for s in exporter.get_finished_spans() if s.name.startswith('ffs.api:'))
    tool = next(s for s in exporter.get_finished_spans() if s.name == 'tool:ffs_send_otp')
    assert output(api)['error_type'] == output(tool)['error_type'] == 'ReadTimeout'
    assert 'test failure' not in json.dumps([dict(s.attributes) for s in exporter.get_finished_spans()]) + summary_logs(caplog)


def test_tracing_failure_does_not_break_workflow(setup, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('telemetry failure')
    monkeypatch.setattr(ffs_tracing, 'get_client', broken)
    monkeypatch.setattr(ffs_tracing.logger, 'log', broken)
    assert asyncio.run(login())['status'] == 'ok'


def test_unloaded_skill_is_traced_without_api_calls(setup, tracing):
    client, exporter = tracing
    result = asyncio.run(ffs_send_otp(context(selective=True), '99999999999', '27'))
    assert result['status'] == 'unavailable' and not setup.backend.calls
    client.flush()
    assert output(exporter.get_finished_spans()[-1])['reason'] == 'skill_not_loaded'


def test_booking_summary_logging_omits_reference_codes(setup, tracing, caplog):
    client, exporter = tracing
    caplog.set_level(logging.INFO, logger='helpers.ffs_tracing')
    async def run():
        village, crops = selection(await login())
        await ffs_get_entitlement(context('3'), village, crops)
        shops = await ffs_find_shops(context('4'), order=[ffs.OrderItem(product='urea', product_id=1089, bags=4)])
        shop = shops['data']['shops'][0]['shop_ref']
        preview = await ffs_create_booking(context('5'), shop)
        result = await ffs_create_booking(context('6'), shop, True)
        assert result['status'] == 'ok'
    asyncio.run(run())
    client.flush()
    spans = exporter.get_finished_spans()
    booking = [s for s in spans if s.name == 'tool:ffs_create_booking']
    assert len(booking) == 2
    assert output(booking[-1])['booking_valid'] is True
    assert output(booking[-1])['bookings_count'] == 1
    serialized = json.dumps([dict(s.attributes) for s in spans]) + summary_logs(caplog)
    assert 'PRC-TEST-001' not in serialized


def test_catalogue_span_keeps_summary_instead_of_returned_prompt(monkeypatch, tracing):
    client, exporter = tracing
    from helpers import scheme_qdrant_search as catalog
    monkeypatch.setattr(importlib.import_module('langfuse._client.observe'), 'get_client', lambda **kw: client)
    monkeypatch.setattr(catalog, 'lf_update_current_observation', lambda **kw: client.update_current_span(**kw))
    monkeypatch.setattr(catalog, 'get_master_catalog_snapshot', lambda tier: {'version': 7, 'entries': []})
    monkeypatch.setattr(catalog, 'get_builtin_scheme_list', lambda: [{'scheme_code': 'test', 'scheme_name': 'FULL-CATALOGUE-TEXT'}])
    prompt = catalog.format_vector_schemes_prompt_block()
    assert 'FULL-CATALOGUE-TEXT' in prompt['vector_schemes_bullets']
    client.flush()
    span = exporter.get_finished_spans()[-1]
    assert span.name == 'prompt:master_catalog_redis_check'
    assert output(span)['merged_scheme_count'] == 1
    assert 'FULL-CATALOGUE-TEXT' not in json.dumps(dict(span.attributes))


def test_chat_agent_span_keeps_final_answer_not_internal_run(monkeypatch, tracing):
    from types import SimpleNamespace
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel
    client, exporter = tracing
    monkeypatch.setenv('AZURE_GPT54_MINI_BASE_URL', 'https://unused.invalid/v1')
    monkeypatch.setenv('AZURE_GPT54_MINI_API_KEY', 'test')
    monkeypatch.setenv('AGRINET_GEMMA_BASE_URL', 'http://unused.invalid/v1')
    monkeypatch.setenv('AGRINET_GEMMA_MODEL_NAME', 'offline-test')
    from app.services import chat
    from app.services.model_routing import ModelRouteDecision
    from helpers import langfuse_tracing
    monkeypatch.setattr(importlib.import_module('langfuse._client.observe'), 'get_client', lambda **kw: client)
    monkeypatch.setattr(langfuse_tracing, 'get_client', lambda: client)
    async def stream(messages, info):
        yield 'Your entitlement is 10 bags.'
    model = FunctionModel(stream_function=stream)
    monkeypatch.setattr(chat, 'agrinet_agent', Agent(model, instrument=False))
    monkeypatch.setattr(chat, 'get_registry', lambda: SimpleNamespace(
        get_model=lambda alias: model, get_kind=lambda alias: 'vllm'))
    result = asyncio.run(chat._run_agrinet_once_streaming(
        'Maize for both plots', [], SimpleNamespace(lang_code='en'), 'test-session', 'test-user',
        'valid_agricultural', ModelRouteDecision('test', 'offline-test', 'test'), False))
    assert result.output_text == 'Your entitlement is 10 bags.'
    client.flush()
    span = next(s for s in exporter.get_finished_spans() if s.name == 'agent.vistaar')
    assert span.attributes['langfuse.observation.output'] == result.output_text
    assert '_state' not in span.attributes['langfuse.observation.output']
