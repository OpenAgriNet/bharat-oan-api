"""Load localized skill instructions and expose their original business tools."""
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool

from agents.skill_catalog import get_catalog
from agents.skill_policy import SkillState
from app.services.session_state import register_section, remember

# Skills loaded with load_skill. Hidden from the model: the system prompt already carries their instructions.
SKILLS = register_section('skills', view=lambda state: None)


def skill_instructions(name: str, state: SkillState) -> str:
    catalog = get_catalog()
    body = catalog.render_skill(name, state.language, state.context, state.agent)
    required = catalog.manifest.skills[name].requires
    if required:
        body = ('Supporting skills (already loaded): ' + ', '.join(required) + '. '
                'Apply this workflow\'s prerequisites and clarification rules before using '
                'supporting tools. Loading skills does not execute their tools.\n\n' + body)
    # Every skill's instructions get a heading and a scope line, so one skill's rules (e.g. mandi's "ask for one
    # commodity") are not applied to another skill's requests (combined eval 2026-11-23, unit 2179).
    return (f'## Skill: {name}\nThe instructions in this section apply only to requests handled by the {name} '
            f'skill.\n\n' + body)


def render_prompt(state: SkillState, language: str, context: dict) -> str:
    catalog = get_catalog()
    # Templates hide text for skills that are not in the manifest's enabled_skills.
    context = {**context, 'enabled_skills': state.permitted}
    state.language = language
    state.context = context
    chunks = [catalog.render_global(language, context, state.agent)]
    active = state.active
    chunks.extend(skill_instructions(name, state) for name in catalog.resolve(active))
    pending = state.permitted - active
    if 'khc.grievances' in pending and context.get('khc_service_note'):
        # KHC status routing must hold before its workflow is loaded (see khc_service_note).
        chunks.append(context['khc_service_note'].strip())
    if pending:
        # Every enabled skill keeps its description, loaded or not, so the model sees the whole map of services.
        summaries = [f'- {name}{" (loaded)" if name in active else ""}: '
                     f'{catalog.render_summary(name, language, context, state.agent)}'
                     for name in catalog.manifest.skills if name in state.permitted]
        chunks.append(
            '## Available skills\nLoad a relevant skill with load_skill before using its workflow. '
            'When the request fits a skill that is not loaded, load it first, before asking the farmer for any '
            'details: the skill says what to ask. '
            'Call only tools whose definitions are available in the current request. '
            'Skill IDs are not tool names or namespaces. Call load_skill on its own and wait for '
            'its result; use the newly exposed business tools in the next step. '
            'Loading is not execution. Reuse information already supplied; ask only for missing or '
            'ambiguous information. Do not narrate loading. Loaded skills remain available for this session '
            'and are marked (loaded): use their instructions above, do not load them again.\n'
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
    remember(ctx, SKILLS, loaded=sorted(state.loaded_skills))
    return {'skill': skill, 'loaded': True, 'instructions': instructions,
            'loaded_skills': newly_loaded, 'loaded_tools': sorted(catalog.tools_for({skill}))}


def restore_loaded_skills(deps: Any) -> None:
    """Skills loaded in earlier turns, from session state (the system prompt then includes their instructions)."""
    saved = (deps.session_state.get(SKILLS) or {}).get('loaded') or []
    deps.skills.loaded_skills |= set(saved) & deps.skills.permitted


SKILL_LOADER = Tool(load_skill, takes_ctx=True, sequential=True, strict=False)


async def prepare_skill_tools(ctx: RunContext[Any], tools: list) -> list:
    state = ctx.deps.skills
    pending = state.permitted - state.active
    visible = state.visible
    # Existing per-tool prepare hooks, including consent checks, have already run.
    return [tool for tool in tools if tool.name in visible
            or (tool.name == 'load_skill' and pending)]
