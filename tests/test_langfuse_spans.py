"""Real Langfuse spans exported to memory; all model traffic stays mocked."""
import asyncio
import importlib
import json
import uuid

import httpx
import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


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
    yield client, exporter
    client.flush()
    provider.shutdown()


def output(span):
    return json.loads(span.attributes['langfuse.observation.output'])


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
