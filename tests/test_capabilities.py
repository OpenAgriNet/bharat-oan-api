"""Offline tests: no model, Redis, business API, or production access required."""
import ast
import asyncio
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents.capabilities import (
    CAPABILITY_LOADER, PROMPT_DIR, capability_prompt_context, capability_routing,
    load_capability, prepare_capability_tools, validate_manifest,
)
from agents.capability_manifest import CAPABILITIES
from agents.capability_policy import ALL_TOOLS, CapabilityState, ToolPolicy, configured_tool_policy
from agents.deps import FarmerContext

ROOT = Path(__file__).resolve().parents[1]
HASHES = json.loads((PROMPT_DIR / 'capabilities/original_sha256.json').read_text())
CONTEXT = dict(today_date='21 October 2026', crop_season='Rabi', last_weekday_table='Tuesday',
               vector_scheme_count=2, vector_schemes_bullets='- Scheme one\n- Scheme two',
               vector_schemes_identifiers='one, two')


def deps_for(allowed=ALL_TOOLS, preloaded=None):
    return FarmerContext(query='test', session_id='offline', lang_code='en',
                         capabilities=CapabilityState(policy=ToolPolicy(
                             allowed_tools=allowed, preloaded_tools=preloaded)))


def test_manifest_covers_registered_tools_exactly():
    tree = ast.parse((ROOT / 'agents/tools/__init__.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'TOOLS' for t in n.targets))
    names = [call.args[0].id for call in assignment.value.elts]
    validate_manifest([SimpleNamespace(name=name) for name in names])
    with pytest.raises(ValueError):
        validate_manifest([SimpleNamespace(name=name) for name in names[:-1]])


@pytest.mark.parametrize('prompt_file', HASHES)
def test_all_loaded_prompt_is_exact_original_in_every_language(prompt_file):
    template = (PROMPT_DIR / f'{prompt_file}.md').read_text()
    def original_fragment(match):
        return (PROMPT_DIR / match[1]).read_text().removesuffix('{# preserve fragment whitespace #}')
    restored = re.sub(r'{% if capability_enabled.*?%}{% include "([^"]+)" %}{% endif %}',
                      original_fragment, template)
    assert hashlib.sha256(restored.encode()).hexdigest() == HASHES[prompt_file]
    env = Environment(loader=FileSystemLoader(PROMPT_DIR), autoescape=False)
    deps = deps_for()
    actual = env.get_template(f'{prompt_file}.md').render(
        **capability_prompt_context(deps, prompt_file, CONTEXT)) + capability_routing(deps)
    assert actual == env.from_string(restored).render(**CONTEXT)
    assert actual == env.get_template(f'{prompt_file}.md').render(**CONTEXT)


@pytest.mark.parametrize('prompt_file', HASHES)
def test_lazy_loading_returns_original_instructions(prompt_file):
    deps = deps_for(preloaded=frozenset())
    capability_prompt_context(deps, prompt_file, CONTEXT)
    sections = json.loads((PROMPT_DIR / 'capabilities/sections.json').read_text())[prompt_file]
    env = Environment(loader=FileSystemLoader(PROMPT_DIR), autoescape=False)
    for group, names in CAPABILITIES.items():
        result = asyncio.run(load_capability(SimpleNamespace(deps=deps), group))
        expected = [env.get_template(s['path']).render(**CONTEXT)
                    for s in sections if set(s['tools']) & names]
        assert result['instructions'] == '\n'.join(expected)
    assert deps.capabilities.visible == ALL_TOOLS


def test_allowlist_and_configuration(monkeypatch):
    with pytest.raises(ValueError):
        ToolPolicy(allowed_tools={'typo'})
    with pytest.raises(ValueError):
        ToolPolicy(allowed_tools=set(), preloaded_tools={'search_terms'})
    monkeypatch.setenv('CAPABILITY_ALLOWED_TOOLS', 'search_terms,search_documents')
    monkeypatch.setenv('CAPABILITY_PRELOADED_TOOLS', 'search_terms')
    policy = configured_tool_policy()
    assert policy.allowed_tools == {'search_terms', 'search_documents'}
    assert policy.preloaded_tools == {'search_terms'}
    monkeypatch.setenv('CAPABILITY_PRELOADED_TOOLS', 'all')
    assert configured_tool_policy().preloaded_tools == policy.allowed_tools
    monkeypatch.setenv('CAPABILITY_ALLOWED_TOOLS', 'none')
    assert configured_tool_policy().allowed_tools == set()


def test_partial_capability_cannot_enable_forbidden_tools():
    deps = deps_for(allowed={'search_terms'}, preloaded=set())
    ctx = SimpleNamespace(deps=deps)
    result = asyncio.run(load_capability(ctx, 'crop_advice'))
    assert result['loaded_tools'] == ['search_terms']
    deps.capabilities.loaded_tools.add('search_documents')
    assert deps.capabilities.visible == {'search_terms'}
    with pytest.raises(ModelRetry):
        asyncio.run(load_capability(ctx, 'pmkisan'))
    with pytest.raises(ModelRetry):
        asyncio.run(load_capability(ctx, 'unknown'))


def test_restore_across_topic_switches_and_policy_changes():
    deps = deps_for(preloaded=set())
    returns = []
    for group in ['pmkisan', 'weather', 'soil_health', 'pmkisan']:
        result = asyncio.run(load_capability(SimpleNamespace(deps=deps), group))
        returns.append(ToolReturnPart(tool_name='load_capability', content=result))
    history = [ModelRequest(parts=returns)]
    fresh = deps_for(preloaded=set())
    fresh.capabilities.restore(history)
    assert fresh.capabilities.visible == deps.capabilities.visible
    narrowed = deps_for(allowed={'weather_forecast'}, preloaded=set())
    narrowed.capabilities.restore(history)
    assert narrowed.capabilities.visible == {'weather_forecast'}
    another_session = deps_for(preloaded=set())
    assert not another_session.capabilities.visible


def test_default_tools_preserve_objects_order_and_hide_loader():
    tools = [SimpleNamespace(name=name) for name in sorted(ALL_TOOLS)]
    ctx = SimpleNamespace(deps=deps_for())
    assert asyncio.run(prepare_capability_tools(ctx, tools+[SimpleNamespace(name='load_capability')])) == tools
    ctx.deps = deps_for(allowed=set())
    assert asyncio.run(prepare_capability_tools(ctx, tools+[SimpleNamespace(name='load_capability')])) == []


def test_real_agent_loader_then_execution_and_existing_prepare_guard():
    calls = []
    def search_terms(query: str) -> str:
        calls.append(query)
        return 'rice'
    async def deny(ctx, definition):
        return None
    def search_documents(query: str) -> str:
        raise AssertionError('Existing prepare guard was bypassed')
    step = 0
    def model(messages, info):
        nonlocal step
        names = {tool.name for tool in info.function_tools}
        assert 'search_documents' not in names
        if step == 0:
            assert names == {'load_capability'}
            response = ModelResponse(parts=[ToolCallPart('load_capability', {'capability': 'crop_advice'})])
        elif step == 1:
            assert names == {'search_terms'}
            response = ModelResponse(parts=[ToolCallPart('search_terms', {'query': 'rice'})])
        else:
            response = ModelResponse(parts=[TextPart('done')])
        step += 1
        return response
    agent = Agent(FunctionModel(model), deps_type=FarmerContext,
                  tools=[Tool(search_terms), Tool(search_documents, prepare=deny), CAPABILITY_LOADER],
                  prepare_tools=prepare_capability_tools)
    deps = deps_for(allowed={'search_terms', 'search_documents'}, preloaded=set())
    assert agent.run_sync('rice', deps=deps).output == 'done'
    assert calls == ['rice']


def test_unavailable_tool_is_not_executed_even_if_model_requests_it():
    def search_documents(query: str) -> str:
        raise AssertionError('Denied tool executed')
    step = 0
    def model(messages, info):
        nonlocal step
        assert not info.function_tools
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart('search_documents', {'query': 'rice'})])
        return ModelResponse(parts=[TextPart('unavailable')])
    agent = Agent(FunctionModel(model), deps_type=FarmerContext,
                  tools=[Tool(search_documents), CAPABILITY_LOADER], prepare_tools=prepare_capability_tools)
    assert agent.run_sync('rice', deps=deps_for(allowed=set())).output == 'unavailable'


@pytest.mark.parametrize('prompt_file', HASHES)
def test_unloaded_workflow_is_removed_but_defaults_restore_it(prompt_file):
    if prompt_file == 'voice_other':
        return  # This fallback contains only shared instructions in the original.
    env = Environment(loader=FileSystemLoader(PROMPT_DIR), autoescape=False)
    deps = deps_for(preloaded=set())
    small = env.get_template(f'{prompt_file}.md').render(
        **capability_prompt_context(deps, prompt_file, CONTEXT))
    full = env.get_template(f'{prompt_file}.md').render(**CONTEXT)
    assert len(small) < len(full)
    for group in CAPABILITIES:
        asyncio.run(load_capability(SimpleNamespace(deps=deps), group))
    restored = env.get_template(f'{prompt_file}.md').render(
        **capability_prompt_context(deps, prompt_file, CONTEXT))
    assert restored == full
