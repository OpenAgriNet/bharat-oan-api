"""Offline contracts for shared routing. Run with python -m pytest tests/test_model_routing.py."""
import asyncio
from collections import Counter
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from agents.model_registry import ModelRegistry
from app.services.model_routing import ModelRouter, parse_concurrency


class MemoryCache:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl):
        self.data[key], self.ttls[key] = value, ttl


def router(tmp_path):
    path = tmp_path / 'models.yaml'
    path.write_text(yaml.safe_dump({
        'models': {
            'local': {'kind': 'vllm', 'model_name': 'gemma', 'base_url': 'http://unused.invalid/v1',
                      'fallback': {'to': 'mini', 'on_error': ['ModelAPIError', 'TimeoutError'],
                                   'on_concurrency_above': 10}},
            'mini': {'kind': 'azure-openai', 'api': 'responses', 'model_name': 'gpt-5.4-mini',
                     'base_url': 'https://unused.invalid/v1', 'api_key': 'test'},
        },
        'use_cases': {
            'agrinet': {'aliases': ['local', 'mini'], 'proportions': [70, 30], 'default_alias': 'mini'},
            'moderation': {'aliases': ['local']},
            'future': {'aliases': ['local']},
        },
    }))
    registry = ModelRegistry(path)
    registry.validate()
    result = ModelRouter(registry, MemoryCache())
    result.concurrency = AsyncMock(return_value=0)
    return result


def test_shared_selection_fallback_and_stream_contract(tmp_path):
    async def check():
        service = router(tmp_path)
        picks = [await service.resolve('agrinet', str(i), randint=lambda a, b: i) for i in range(1, 101)]
        assert Counter(p.route for p in picks) == {'local': 70, 'mini': 30}
        assert (await service.resolve('agrinet', '1', randint=lambda a, b: 100)).route == 'local'
        assert (await service.resolve('agrinet', 'old', has_history=True)).route == 'mini'
        # Fallback target comes from the alias, not the use-case default (which is local here).
        service.concurrency.return_value = 11
        assert (await service.resolve('moderation', 's')).route == 'mini'
        assert service.cache.data['s_MODERATION_ROUTE'] == 'local'
        service.concurrency.return_value = 0
        assert (await service.resolve('moderation', 's')).route == 'local'
        calls = []
        async def fail_primary(decision):
            calls.append(decision.route)
            if decision.route == 'local':
                raise ModelHTTPError(503, 'gemma')
            return 'ok'
        output, final, fallback = await service.run('moderation', 's', fail_primary)
        assert (output, final.route, fallback, calls) == ('ok', 'mini', True, ['local', 'mini'])
        assert (await service.resolve('moderation', 's')).route == 'mini'  # fallback-only alias accepted
        assert service.cache.data['s_MODERATION_ROUTE'] == 'mini'
        assert (await service.resolve('agrinet', 's', randint=lambda a, b: 1)).route == 'local'
        # A third use case inherits all policies without another router implementation.
        assert (await service.run('future', 's', fail_primary))[1].route == 'mini'
        assert service.cache.ttls['s_FUTURE_ROUTE'] == 7200
        # Do not repeat a failed secondary or persist an unsuccessful error fallback.
        calls.clear()
        async def fail_both(decision):
            calls.append(decision.route)
            raise ModelHTTPError(503, decision.route)
        with pytest.raises(ModelHTTPError):
            await service.run('moderation', 'all-failed', fail_both)
        assert calls == ['local', 'mini']
        assert service.cache.data['all-failed_MODERATION_ROUTE'] == 'local'
        # A started output stream, programmer errors, and cancellation are never retried.
        calls.clear()
        with pytest.raises(ModelHTTPError):
            await service.run('moderation', 'stream', fail_primary, can_fallback=lambda: False)
        assert calls == ['local']
        for error in [ValueError('bug'), asyncio.CancelledError()]:
            calls.clear()
            async def bad(decision):
                calls.append(decision.route)
                raise error
            with pytest.raises(type(error)):
                await service.run('moderation', 'bad', bad)
            assert calls == ['local']
        # Deadline covers the whole attempt, not just the HTTP connection.
        service.registry._use_cases_cfg['moderation']['timeout_seconds'] = .01
        async def slow(decision):
            if decision.route == 'local':
                await asyncio.sleep(1)
            return 'fast'
        assert (await service.run('moderation', 'timeout', slow))[0] == 'fast'
        # Unknown capacity fails over transiently; equality is not 'above'.
        service.concurrency.return_value = None
        assert (await service.resolve('moderation', 'unknown')).route == 'mini'
        assert service.cache.data['unknown_MODERATION_ROUTE'] == 'local'
        service.concurrency.return_value = 10
        assert (await service.resolve('moderation', 'equal')).route == 'local'
        # The same executor runs a real Pydantic agent and retains its result type.
        async def broken(messages, info):
            raise ModelHTTPError(503, 'gemma')
        async def good(messages, info):
            return ModelResponse(parts=[TextPart('fallback response')])
        service.registry._model_cache.update(local=FunctionModel(broken), mini=FunctionModel(good))
        service.registry._use_cases_cfg['moderation']['timeout_seconds'] = 5
        result, decision, used = await service.run_agent('moderation', 'real-agent', Agent(), 'hello')
        assert result.output == 'fallback response' and decision.route == 'mini' and used
    asyncio.run(check())


@pytest.mark.parametrize('text, expected', [
    ('vllm:num_requests_running 3\nvllm:num_requests_waiting 2', 5),
    ('vllm:num_requests_running{model_name="x"} 3\nvllm:num_requests_waiting{model_name="x"} 2', 5),
    ('vllm:num_requests_running 0\nvllm:num_requests_waiting 0', 0),
    ('vllm:num_requests_running NaN\nvllm:num_requests_waiting 0', None),
    ('vllm:num_requests_running -1\nvllm:num_requests_waiting 0', None),
    ('vllm:num_requests_running 3', None), ('garbage', None),
])
def test_metrics(text, expected):
    assert parse_concurrency(text) == expected


@pytest.mark.parametrize('mutation, match', [
    (lambda r: r._models_cfg['mini'].update(fallback={'to': 'local', 'on_error': ['TimeoutError']}), 'cycle'),
    (lambda r: r._models_cfg['local']['fallback'].update(to='missing'), 'Unknown'),
    (lambda r: r._models_cfg['local']['fallback'].update(on_error=['Typo']), 'unknown fallback error'),
    (lambda r: r._use_cases_cfg['agrinet'].update(proportions=[80, 30]), 'sum to 100'),
    (lambda r: r._use_cases_cfg['agrinet'].update(timeout_seconds=float('nan')), 'finite'),
    (lambda r: r._models_cfg['mini'].update(api_key=''), 'api_key is required'),
])
def test_bad_config_fails_before_serving(tmp_path, mutation, match):
    registry = router(tmp_path).registry
    mutation(registry)
    registry._model_cache.clear()
    with pytest.raises(ValueError, match=match):
        registry.validate()
