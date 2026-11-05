"""Dependency loading through actual session policy and the Pydantic agent loop."""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents import skills
from agents.skill_policy import SkillState, resolve_selection
from tests.test_skills import CAT, CONTEXT, LANGUAGES, temporary_catalog


@pytest.mark.parametrize('language', LANGUAGES)
def test_mandi_loads_location_once_without_another_model_call(language):
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    skills.render_prompt(state, language, CONTEXT)
    ctx = SimpleNamespace(deps=SimpleNamespace(skills=state))
    result = asyncio.run(skills.load_skill(ctx, 'mandi.prices'))
    assert result['loaded_skills'] == ['location.resolve', 'mandi.prices']
    for name in result['loaded_skills']:
        assert result['instructions'].count(CAT.render_skill(name, language, CONTEXT)) == 1
    assert state.visible == {'forward_geocode', 'reverse_geocode', 'search_commodity', 'get_mandi_prices'}
    rendered = skills.render_prompt(state, language, CONTEXT)
    assert '## Available skills' not in rendered
    assert rendered.count(CAT.render_skill('location.resolve', language, CONTEXT)) == 1
    fresh = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    fresh.restore([ModelRequest(parts=[ToolReturnPart('load_skill', result)])])
    assert fresh.active == state.active


def test_existing_dependency_is_not_returned_twice():
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=['location.resolve'])
    skills.render_prompt(state, 'en', CONTEXT)
    result = asyncio.run(skills.load_skill(SimpleNamespace(deps=SimpleNamespace(skills=state)), 'mandi.prices'))
    assert result['loaded_skills'] == ['mandi.prices']
    assert CAT.render_skill('location.resolve', 'en', CONTEXT) not in result['instructions']


def test_old_capability_history_restores_permitted_workflow_dependencies():
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    result = {'capability': 'mandi', 'loaded_tools': ['forward_geocode', 'search_commodity', 'get_mandi_prices']}
    state.restore([ModelRequest(parts=[ToolReturnPart('load_capability', result)])])
    assert state.active == {'mandi.prices', 'location.resolve'}
    denied = SkillState(enabled_skills=[], preloaded_skills=[])
    denied.restore([ModelRequest(parts=[ToolReturnPart('load_capability', result)])])
    assert not denied.active


def test_documents_shared_by_two_workflows_load_once():
    state = SkillState(enabled_skills=['crop.advice', 'schemes.information'], preloaded_skills=[])
    skills.render_prompt(state, 'hi', CONTEXT)
    ctx = SimpleNamespace(deps=SimpleNamespace(skills=state))
    first = asyncio.run(skills.load_skill(ctx, 'crop.advice'))
    second = asyncio.run(skills.load_skill(ctx, 'schemes.information'))
    assert first['loaded_skills'] == ['documents.search', 'crop.advice']
    assert second['loaded_skills'] == ['schemes.information']
    assert skills.render_prompt(state, 'hi', CONTEXT).count('### Document search') == 1


@pytest.mark.parametrize('requires,error', [
    (['missing.skill'], 'Unknown required'),
    (['mandi.prices'], 'cycle'),
    (['location.resolve', 'location.resolve'], 'Duplicate required'),
])
def test_invalid_dependency_rejected(tmp_path, requires, error):
    with pytest.raises(ValueError, match=error):
        temporary_catalog(tmp_path, lambda m, root: m['skills']['mandi.prices'].update(requires=requires))


def test_cycle_and_environment_denials(tmp_path):
    def cycle(m, root):
        m['skills']['location.resolve']['requires'] = ['mandi.prices']
    with pytest.raises(ValueError, match='cycle'):
        temporary_catalog(tmp_path / 'cycle', cycle)
    def denied(m, root):
        m['environments'] = {'prod': {'allowed_skills': ['mandi.prices']}}
    with pytest.raises(ValueError, match='requires denied skills'):
        temporary_catalog(tmp_path / 'denied', denied)


def test_environment_tools_cannot_be_reenabled_through_dependency(tmp_path):
    def restrict(m, root):
        m['environments'] = {'prod': {'allowed_tools': ['search_commodity', 'get_mandi_prices']}}
    cat = temporary_catalog(tmp_path, restrict)
    permitted, _ = resolve_selection(cat, 'chat', 'prod', None, None)
    assert 'mandi.prices' not in permitted and 'location.resolve' not in permitted
    with pytest.raises(ValueError, match='disallowed'):
        resolve_selection(cat, 'chat', 'prod', ['mandi.prices'], [])


def test_transitive_diamond_dependencies(tmp_path):
    def diamond(m, root):
        m['skills']['location.resolve']['requires'] = ['documents.search']
        m['skills']['mandi.prices']['requires'] = ['weather.forecast', 'location.resolve']
    cat = temporary_catalog(tmp_path, diamond)
    order = cat.resolve({'mandi.prices'})
    assert order == ['documents.search', 'location.resolve', 'weather.forecast', 'mandi.prices']


def test_failed_render_does_not_partially_load(monkeypatch):
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    skills.render_prompt(state, 'en', CONTEXT)
    original = skills.skill_instructions
    def render(name, state):
        if name == 'mandi.prices':
            raise ValueError('broken prompt')
        return original(name, state)
    monkeypatch.setattr(skills, 'skill_instructions', render)
    with pytest.raises(ValueError, match='broken prompt'):
        asyncio.run(skills.load_skill(SimpleNamespace(deps=SimpleNamespace(skills=state)), 'mandi.prices'))
    assert not state.active


def test_real_loop_exposes_supporting_tools_on_next_step():
    executed, seen = [], []
    def forward_geocode(place: str):
        executed.append(place)
        return {'latitude': 25.6, 'longitude': 85.1}
    def model(messages, info):
        seen.append({t.name for t in info.function_tools})
        if len(seen) == 1:
            assert seen[-1] == {'load_skill'}
            return ModelResponse(parts=[ToolCallPart('load_skill', {'skill': 'mandi.prices'})])
        assert seen[-1] == {'forward_geocode'}
        if len(seen) == 2:
            result = messages[-1].parts[0].content
            assert result['loaded_skills'] == ['location.resolve', 'mandi.prices']
            assert 'Date-first rule' in result['instructions']
            assert 'Location-based queries' in result['instructions']
            return ModelResponse(parts=[ToolCallPart('forward_geocode', {'place': 'Patna'})])
        return ModelResponse(parts=[TextPart('Location resolved')])
    agent = Agent(FunctionModel(model), tools=[Tool(forward_geocode), skills.SKILL_LOADER],
                  prepare_tools=skills.prepare_skill_tools)
    @agent.system_prompt(dynamic=True)
    def prompt(ctx):
        return skills.render_prompt(ctx.deps.skills, 'en', CONTEXT)
    state = SkillState(enabled_skills=['mandi.prices'], preloaded_skills=[])
    assert agent.run_sync('Wheat prices in Patna today', deps=SimpleNamespace(skills=state)).output == 'Location resolved'
    assert executed == ['Patna'] and len(seen) == 3


@pytest.mark.parametrize('skill,own,other', [
    ('pm_kisan.grievances', 'pmkisan_submit_grievance', 'pmfby_submit_grievance'),
    ('pmfby.grievances', 'pmfby_submit_grievance', 'pmkisan_submit_grievance'),
    ('aif.status', 'check_aif_grievance_status', 'pmfby_submit_grievance'),
])
def test_scheme_grievances_do_not_load_other_schemes(skill, own, other):
    """A scheme loads its own tools plus shared KHC fallback, not every scheme."""
    state = SkillState(enabled_skills=[skill])
    assert own in state.visible
    assert other not in state.visible
    assert 'register_khc_grievance' in state.visible
    assert 'khc.grievances' in state.active
    assert 'grievances.manage' not in CAT.manifest.skills


def test_per_skill_enabled_flag_is_rejected(tmp_path):
    # Fail explicitly rather than silently ignoring a retired permission control.
    with pytest.raises(ValueError, match='Extra inputs are not permitted'):
        temporary_catalog(tmp_path, lambda m, root: m['skills']['location.resolve'].update(enabled=False))
