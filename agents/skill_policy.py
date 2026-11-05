"""Session selection over the manifest. HTTP input is deliberately not wired yet."""
import logging
import os
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agents.skill_catalog import Selection, SkillCatalog, get_catalog, select

logger = logging.getLogger(__name__)


def resolve_selection(catalog: SkillCatalog, agent: str, environment: str | None,
                      enabled: Selection | None, preloaded: Selection | None) -> tuple[set[str], set[str]]:
    for key in ('CAPABILITY_ALLOWED_TOOLS', 'CAPABILITY_PRELOADED_TOOLS'):
        if os.environ.get(key, 'all').strip().lower() != 'all':
            raise ValueError(f'{key} is retired; configure skills in assets/prompts/manifest.yaml')
    m = catalog.manifest
    if agent not in m.agents:
        raise ValueError(f'Unknown agent: {agent}')
    config = m.agents[agent]
    available = select(config.enabled_skills, set(m.skills), 'agent skills')
    available = set(catalog.resolve(available))
    if m.environments:
        if environment not in m.environments:
            raise ValueError('Select a known SKILL_ENVIRONMENT when environment restrictions are configured')
        env = m.environments[environment]
        available &= select(env.allowed_skills, set(m.skills), 'environment skills')
        tools = select(env.allowed_tools, catalog.tools, 'environment tools')
        omitted = {name for name in available
                   if not set(catalog.resolve({name})) <= available
                   or not catalog.tools_for({name}) <= tools}
        if omitted:
            logger.warning('Skills unavailable because dependencies or tools are denied: %s', sorted(omitted))
        available -= omitted
    elif environment:
        raise ValueError(f'No environments configured; unknown selection {environment}')
    selected = available if enabled is None else select(enabled, available, 'session skills')
    selected = set(catalog.resolve(selected))
    if not selected <= available:
        raise ValueError(f'Session requires denied skills: {sorted(selected - available)}')
    if preloaded is None:
        defaults = select(config.preloaded_skills, set(m.skills), 'preloaded skills')
        initial = defaults & selected
    else:
        initial = select(preloaded, selected, 'session preloaded skills')
    return selected, set(catalog.resolve(initial))


class SkillState(BaseModel):
    agent: str = 'chat'
    environment: str | None = Field(default_factory=lambda: os.environ.get('SKILL_ENVIRONMENT') or None)
    enabled_skills: Selection | None = None
    preloaded_skills: Selection | None = None
    loaded_skills: set[str] = Field(default_factory=set)
    # Fresh per-turn rendering context; never persisted as session permissions.
    context: dict[str, Any] = Field(default_factory=dict, exclude=True)
    language: str = 'en'

    @model_validator(mode='after')
    def validate_selection(self):
        self.selection()
        return self

    def selection(self) -> tuple[set[str], set[str]]:
        return resolve_selection(get_catalog(), self.agent, self.environment,
                                 self.enabled_skills, self.preloaded_skills)

    @property
    def permitted(self) -> set[str]:
        return self.selection()[0]

    @property
    def active(self) -> set[str]:
        permitted, initial = self.selection()
        return set(get_catalog().resolve((initial | self.loaded_skills) & permitted))

    @property
    def allowed_tools(self) -> set[str]:
        m = get_catalog().manifest
        return {tool for name in self.permitted for tool in m.skills[name].tools}

    @property
    def visible(self) -> set[str]:
        m = get_catalog().manifest
        return {tool for name in self.active for tool in m.skills[name].tools}

    def restore(self, history: list) -> None:
        permitted = self.permitted
        for message in history:
            for part in getattr(message, 'parts', ()):
                if getattr(part, 'part_kind', None) != 'tool-return':
                    continue
                self._restore_tool_return(part, permitted)
        self.loaded_skills &= permitted

    def _restore_tool_return(self, part: Any, permitted: set[str]) -> None:
        content = getattr(part, 'content', None)
        if not isinstance(content, dict):
            return
        if part.tool_name == 'load_skill':
            skill = content.get('skill')
            if isinstance(skill, str) and skill in permitted and content.get('loaded') is True:
                self.loaded_skills.add(skill)
        elif part.tool_name == 'load_capability':
            self._restore_capability(content, permitted)

    def _restore_capability(self, content: dict, permitted: set[str]) -> None:
        # Compatibility for sessions begun before migration. Partial workflows
        # cannot be restored; history never widens current permissions.
        group = content.get('capability')
        tools = content.get('loaded_tools')
        if not isinstance(group, str) or not isinstance(tools, list):
            return
        if not all(isinstance(tool, str) for tool in tools):
            return
        aliases = {'pmkisan': 'pm_kisan', 'crop_advice': 'crop',
                   'schemes': 'schemes', 'amul': 'schemes', 'maha': 'schemes'}
        prefix = aliases.get(group, group) + '.'
        loaded_tools = set(tools)
        for name in permitted:
            bound = set(get_catalog().manifest.skills[name].tools)
            if name.startswith(prefix) and bound and bound <= loaded_tools:
                self.loaded_skills.add(name)
