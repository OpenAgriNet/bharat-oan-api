"""Load localized skill instructions and expose their original business tools."""
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool

from agents.skill_catalog import get_catalog
from agents.skill_policy import SkillState


def render_prompt(state: SkillState, language: str, context: dict) -> str:
    catalog = get_catalog()
    state.language = language
    state.context = context
    chunks = [catalog.render_global(language, context, state.agent)]
    active = state.active
    chunks.extend(catalog.render_skill(name, language, context, state.agent)
                  for name in catalog.manifest.skills if name in active)
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
    """Load one enabled skill's instructions and tools. This performs no business action."""
    state = ctx.deps.skills
    if skill not in state.permitted:
        raise ModelRetry('Choose a skill from the available skill index.')
    if skill in state.active:
        raise ModelRetry('This skill is already loaded. Use its available tools or choose another pending skill.')
    instructions = get_catalog().render_skill(skill, state.language, state.context, state.agent)
    state.loaded_skills.add(skill)
    return {'skill': skill, 'loaded': True, 'instructions': instructions,
            'loaded_tools': sorted(get_catalog().manifest.skills[skill].tools)}


SKILL_LOADER = Tool(load_skill, takes_ctx=True, sequential=True, strict=False)


async def prepare_skill_tools(ctx: RunContext[Any], tools: list) -> list:
    state = ctx.deps.skills
    pending = state.permitted - state.active
    visible = state.visible
    # Existing per-tool prepare hooks, including consent checks, have already run.
    return [tool for tool in tools if tool.name in visible
            or (tool.name == 'load_skill' and pending)]
