"""Skill policies, assets, and real Pydantic tool-loop integration; no services needed."""
import asyncio
import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents.skill_catalog import PROMPT_DIR, SkillCatalog, get_catalog
from agents.skill_policy import SkillState, resolve_selection
from agents.skills import SKILL_LOADER, load_skill, prepare_skill_tools, render_prompt

CAT = get_catalog()
LANGUAGES = CAT.manifest.agents['chat'].languages
CONTEXT = dict(today_date='04 November 2026', crop_season='Rabi', last_weekday_table='Tuesday',
               vector_scheme_count=2, vector_schemes_bullets='- Scheme one\n- Scheme two',
               vector_schemes_identifiers='one, two')


def test_registry_covers_all_tools_with_shared_bindings():
    tree = ast.parse((PROMPT_DIR.parents[1] / 'agents/tools/__init__.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'TOOLS' for t in n.targets))
    names = [call.args[0].id for call in assignment.value.elts]
    CAT.validate_tools([SimpleNamespace(name=name) for name in names])
    assert len(names) == 51
    assert 'forward_geocode' in CAT.manifest.skills['mandi.prices'].tools
    assert 'forward_geocode' in CAT.manifest.skills['weather.forecast'].tools
    with pytest.raises(ValueError):
        CAT.validate_tools([SimpleNamespace(name=name) for name in names[:-1]])


@pytest.mark.parametrize('language', LANGUAGES)
def test_all_loaded_hides_discovery_and_contains_all_instructions(language):
    state = SkillState()
    actual = render_prompt(state, language, CONTEXT)
    assert state.visible == CAT.tools
    assert '## Available skills' not in actual
    for name in CAT.manifest.skills:
        assert CAT.render_skill(name, language, CONTEXT).strip() in actual
    tools = [SimpleNamespace(name=name) for name in [*CAT.tools, 'load_skill']]
    visible = asyncio.run(prepare_skill_tools(SimpleNamespace(deps=SimpleNamespace(skills=state)), tools))
    assert {t.name for t in visible} == CAT.tools


@pytest.mark.parametrize('language', LANGUAGES)
def test_every_skill_loads_instructions_in_selected_language(language):
    state = SkillState(preloaded_skills=[])
    prompt = render_prompt(state, language, CONTEXT)
    assert '## Available skills' in prompt
    ctx = SimpleNamespace(deps=SimpleNamespace(skills=state))
    for name in CAT.manifest.skills:
        assert f'- {name}:' in prompt
        output = asyncio.run(load_skill(ctx, name))
        assert output['instructions'] == CAT.render_skill(name, language, CONTEXT)
        assert set(output['loaded_tools']) == set(CAT.manifest.skills[name].tools)
    assert state.visible == CAT.tools
    assert '## Available skills' not in render_prompt(state, language, CONTEXT)


def test_empty_subsets_and_invalid_requests():
    assert not SkillState(enabled_skills=[]).visible
    assert not SkillState(preloaded_skills=[]).visible
    assert SkillState(enabled_skills=['mandi.prices']).visible == set(CAT.manifest.skills['mandi.prices'].tools)
    for kwargs in [dict(enabled_skills=['typo']), dict(enabled_skills=[], preloaded_skills=['mandi.prices']),
                   dict(agent='typo'), dict(environment='prod')]:
        with pytest.raises(ValueError):
            SkillState(**kwargs)
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    ctx = SimpleNamespace(deps=SimpleNamespace(skills=state))
    with pytest.raises(ModelRetry):
        asyncio.run(load_skill(ctx, 'khc.grievances'))
    text = render_prompt(state, 'hi', CONTEXT)
    assert '- mandi.prices:' in text and '- khc.grievances:' not in text
    assert 'Kisan Help Center (KHC) grievances' not in text


def test_history_reuses_skills_without_leaking_between_sessions():
    state = SkillState(preloaded_skills=[])
    ctx = SimpleNamespace(deps=SimpleNamespace(skills=state))
    render_prompt(state, 'en', CONTEXT)
    result = asyncio.run(load_skill(ctx, 'mandi.prices'))
    history = [ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=result)])]
    fresh = SkillState(preloaded_skills=[])
    fresh.restore(history)
    assert fresh.active == {'mandi.prices'}
    assert not SkillState(preloaded_skills=[]).active
    restricted = SkillState(enabled_skills=['weather.forecast'], preloaded_skills=[])
    restricted.restore(history)
    assert not restricted.active
    for content in [{'skill': ['mandi.prices'], 'loaded': True}, {'skill': 'mandi.prices', 'loaded': False}, 'bad']:
        restricted.restore([ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=content)])])
    assert not restricted.active


def test_real_pydantic_loop_loads_then_calls_and_restores_on_next_turn():
    calls = []
    step = 0
    async def weather_forecast():
        calls.append('weather')
        return 'Rain tomorrow'
    async def forward_geocode():
        return '18.5, 73.8'
    def model(messages, info):
        nonlocal step
        step += 1
        names = {t.name for t in info.function_tools}
        if step == 1:
            assert names == {'load_skill'}
            return ModelResponse(parts=[ToolCallPart('load_skill', {'skill': 'weather.forecast'})])
        assert names == {'weather_forecast', 'forward_geocode'}
        if step == 2:
            assert 'Present weather data' in str(messages[-1].parts)
            return ModelResponse(parts=[ToolCallPart('weather_forecast', {})])
        return ModelResponse(parts=[TextPart('Rain tomorrow')])
    agent = Agent(FunctionModel(model), tools=[Tool(weather_forecast), Tool(forward_geocode), SKILL_LOADER],
                  prepare_tools=prepare_skill_tools)
    @agent.system_prompt(dynamic=True)
    def prompt(ctx):
        ctx.deps.skills.restore(ctx.messages)
        return render_prompt(ctx.deps.skills, 'en', CONTEXT)
    deps = SimpleNamespace(skills=SkillState(enabled_skills=['weather.forecast'], preloaded_skills=[]))
    result = agent.run_sync('Tomorrow weather?', deps=deps)
    assert calls == ['weather']
    new = SimpleNamespace(skills=SkillState(enabled_skills=['weather.forecast'], preloaded_skills=[]))
    agent.run_sync('Thanks', deps=new, message_history=result.all_messages())
    assert new.skills.active == {'weather.forecast'}


def temporary_catalog(tmp_path, mutate):
    import shutil
    shutil.copytree(PROMPT_DIR, tmp_path / 'prompts')
    root = tmp_path / 'prompts'
    data = yaml.safe_load((root / 'manifest.yaml').read_text())
    mutate(data, root)
    (root / 'manifest.yaml').write_text(yaml.safe_dump(data))
    return SkillCatalog(root)


def test_optional_environment_restrictions(tmp_path):
    def change(m, root):
        m['environments'] = {'prod': {'allowed_skills': 'all', 'allowed_tools': ['forward_geocode', 'weather_forecast']}}
    cat = temporary_catalog(tmp_path, change)
    permitted, loaded = resolve_selection(cat, 'chat', 'prod', None, None)
    assert permitted == loaded == {'weather.forecast', 'pm_kisan.installments'}
    with pytest.raises(ValueError):
        resolve_selection(cat, 'chat', None, None, None)
    with pytest.raises(ValueError):
        resolve_selection(cat, 'chat', 'prod', ['mandi.prices'], None)


def test_explicit_broken_dependency_rejected(tmp_path):
    def change(m, root):
        m['environments'] = {'prod': {'allowed_skills': ['mandi.prices'], 'allowed_tools': ['get_mandi_prices']}}
    with pytest.raises(ValueError, match='requires denied tools'):
        temporary_catalog(tmp_path, change)


def test_agent_variants_replace_both_summary_and_body(tmp_path):
    def change(m, root):
        m['agents']['voice'] = {**copy.deepcopy(m['agents']['chat']), 'enabled_skills': ['mandi.prices'],
                                'languages': ['en'], 'skill_variants': {'mandi.prices': 'voice'}}
        folder = root / 'skills/mandi/prices/voice'
        folder.mkdir()
        (folder / 'prices_en.md').write_text('---\nsummary: Spoken mandi prices\n---\nVoice instructions only\n')
    cat = temporary_catalog(tmp_path, change)
    assert cat.document('mandi.prices', 'en', 'voice') == ('Spoken mandi prices', 'Voice instructions only\n')
    assert cat.document('mandi.prices', 'en')[0] != 'Spoken mandi prices'
    (cat.root / 'skills/mandi/prices/voice/prices_en.md').unlink()
    with pytest.raises(FileNotFoundError):
        SkillCatalog(cat.root)


def test_duplicate_manifest_keys_rejected(tmp_path):
    (tmp_path / 'manifest.yaml').write_text('skills: {}\nskills: {}\nagents: {}\n')
    with pytest.raises(ValueError, match='Duplicate YAML key'):
        SkillCatalog(tmp_path)


def test_tool_prepare_guards_and_execution_permissions():
    def forbidden():
        raise AssertionError('Hidden tool executed')
    async def deny(ctx, definition):
        return None
    step = 0
    def model(messages, info):
        nonlocal step
        assert not info.function_tools
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart('forbidden', {})])
        return ModelResponse(parts=[TextPart('Unavailable')])
    agent = Agent(FunctionModel(model), tools=[Tool(forbidden, prepare=deny), SKILL_LOADER],
                  prepare_tools=prepare_skill_tools)
    result = agent.run_sync('Try it', deps=SimpleNamespace(skills=SkillState(enabled_skills=[])))
    assert result.output == 'Unavailable'


def test_retired_environment_filter_cannot_silently_widen_permissions(monkeypatch):
    monkeypatch.setenv('CAPABILITY_ALLOWED_TOOLS', 'weather_forecast')
    with pytest.raises(ValueError, match='retired'):
        SkillState()
