"""Load localized skill instructions and expose their original business tools."""
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool

from agents.skill_catalog import get_catalog
from agents.skill_policy import SkillState


def skill_instructions(name: str, state: SkillState) -> str:
    catalog = get_catalog()
    body = catalog.render_skill(name, state.language, state.context, state.agent)
    required = catalog.manifest.skills[name].requires
    if required:
        body = ('Supporting skills (already loaded): ' + ', '.join(required) + '. '
                'Apply this workflow\'s prerequisites and clarification rules before using '
                'supporting tools. Loading skills does not execute their tools.\n\n' + body)
    return body


def render_prompt(state: SkillState, language: str, context: dict) -> str:
    catalog = get_catalog()
    state.language = language
    state.context = context
    chunks = [catalog.render_global(language, context, state.agent)]
    active = state.active
    chunks.extend(skill_instructions(name, state) for name in catalog.resolve(active))
    pending = state.permitted - active
    if pending:
        summaries = [f'- {name}: {catalog.render_summary(name, language, context, state.agent)}'
                     for name in catalog.manifest.skills if name in pending]
        chunks.append(
            '## Available skills\nLoad a relevant skill with load_skill before using its workflow. '
            'Loading is not execution. Reuse information already supplied; ask only for missing or '
            'ambiguous information. Do not narrate loading. Loaded skills remain available for this session.\n'
            + '\n'.join(summaries))
    if state.permitted != set(catalog.manifest.skills):
        chunks.append('Only enabled skills and their available tools may be used or offered. '
                      'Other services mentioned in workflow instructions are unavailable in this session. '
                      'Do not invent their results.')
    return '\n\n'.join(chunks)


async def load_skill(ctx: RunContext[Any], skill: str) -> dict:
    """Load an enabled skill and its required skills together. Performs no business action."""
    state = ctx.deps.skills
    if skill not in state.permitted:
        raise ModelRetry('Choose a skill from the available skill index.')
    if skill in state.active:
        raise ModelRetry('This skill is already loaded. Use its available tools or choose another pending skill.')
    catalog = get_catalog()
    required = catalog.resolve({skill})
    if not set(required) <= state.permitted:
        raise ModelRetry('A required skill is unavailable for this session.')
    newly_loaded = [name for name in required if name not in state.active]
    # Render everything before mutating state: a bad dependency must not partially load.
    instructions = '\n\n'.join(skill_instructions(name, state) for name in newly_loaded)
    state.loaded_skills.update(newly_loaded)
    return {'skill': skill, 'loaded': True, 'instructions': instructions,
            'loaded_skills': newly_loaded, 'loaded_tools': sorted(catalog.tools_for({skill}))}


SKILL_LOADER = Tool(load_skill, takes_ctx=True, sequential=True, strict=False)


async def prepare_skill_tools(ctx: RunContext[Any], tools: list) -> list:
    state = ctx.deps.skills
    pending = state.permitted - state.active
    visible = state.visible
    # Existing per-tool prepare hooks, including consent checks, have already run.
    return [tool for tool in tools if tool.name in visible
            or (tool.name == 'load_skill' and pending)]
