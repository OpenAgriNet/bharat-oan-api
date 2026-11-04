"""Manifest and localized skill assets, independent of the agent framework."""
from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, ConfigDict, Field
import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / 'assets/prompts'
Selection = Literal['all'] | list[str]


class UniqueKeysLoader(yaml.SafeLoader):
    """Fail on duplicate YAML keys instead of silently losing a skill definition."""


def _mapping(loader, node, deep=False):
    pairs = loader.construct_pairs(node, deep=deep)
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate YAML key: {key}')
        result[key] = value
    return result


UniqueKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


class Definition(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Skill(Definition):
    prompt_dir: str
    enabled: bool = True
    tools: list[str]


class AgentConfig(Definition):
    prompt_dir: str
    languages: list[str]
    enabled_skills: Selection = 'all'
    preloaded_skills: Selection = 'all'
    skill_variants: dict[str, str] = Field(default_factory=dict)


class EnvironmentConfig(Definition):
    allowed_skills: Selection = 'all'
    allowed_tools: Selection = 'all'


class Manifest(Definition):
    skills: dict[str, Skill]
    agents: dict[str, AgentConfig]
    environments: dict[str, EnvironmentConfig] = Field(default_factory=dict)


def select(value: Selection, available: set[str], label: str) -> set[str]:
    if value == 'all':
        return set(available)
    if len(value) != len(set(value)):
        raise ValueError(f'Duplicate {label}')
    unknown = set(value) - available
    if unknown:
        raise ValueError(f'Unknown or disallowed {label}: {sorted(unknown)}')
    return set(value)


class SkillCatalog:
    def __init__(self, root: Path = PROMPT_DIR):
        self.root = root.resolve()
        self.manifest = Manifest.model_validate(yaml.load(
            (self.root / 'manifest.yaml').read_text(), Loader=UniqueKeysLoader))
        self.env = Environment(loader=FileSystemLoader(self.root), autoescape=False)
        self.validate()

    @property
    def tools(self) -> set[str]:
        return {name for skill in self.manifest.skills.values() for name in skill.tools}

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f'Prompt path outside assets: {relative}')
        return path

    @lru_cache(maxsize=1024)
    def document(self, skill_id: str, language: str, agent: str = 'chat') -> tuple[str, str]:
        config = self.manifest.agents[agent]
        if language not in config.languages:
            raise ValueError(f'Unsupported language {language!r} for {agent}')
        folder = self.path(self.manifest.skills[skill_id].prompt_dir)
        stem = folder.name
        variant = config.skill_variants.get(skill_id)
        if variant:
            folder = self.path(str(folder.relative_to(self.root) / variant))
        text = (folder / f'{stem}_{language}.md').read_text()
        match = re.match(r'\A---\n(.*?)\n---\n(.*)\Z', text, re.S)
        if not match:
            raise ValueError(f'Missing summary front matter: {folder}/{stem}_{language}.md')
        metadata = yaml.load(match[1], Loader=UniqueKeysLoader)
        if not isinstance(metadata, dict) or set(metadata) != {'summary'} or not isinstance(metadata['summary'], str) or not metadata['summary'].strip() or not match[2].strip():
            raise ValueError(f'Expected a nonempty summary and body: {skill_id}/{language}')
        return metadata['summary'], match[2]

    def render_skill(self, skill_id: str, language: str, context: dict, agent: str = 'chat') -> str:
        _, body = self.document(skill_id, language, agent)
        return self.env.from_string(body).render(**context)

    def render_global(self, language: str, context: dict, agent: str = 'chat') -> str:
        config = self.manifest.agents[agent]
        if language not in config.languages:
            raise ValueError(f'Unsupported language {language!r} for {agent}')
        return self.env.get_template(f'{config.prompt_dir}/agrinet_{language}.md').render(**context)

    def validate(self) -> None:
        m = self.manifest
        if not m.skills or not m.agents:
            raise ValueError('Manifest must define skills and agents')
        folders = []
        for name, skill in m.skills.items():
            if not re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+', name):
                raise ValueError(f'Invalid skill ID: {name}')
            folder = self.path(skill.prompt_dir)
            if not folder.is_dir() or len(skill.tools) != len(set(skill.tools)):
                raise ValueError(f'Missing folder or duplicate tool binding: {name}')
            folders.append(folder)
        if len(folders) != len(set(folders)):
            raise ValueError('Each skill needs its own prompt folder')
        for agent, config in m.agents.items():
            enabled = select(config.enabled_skills, set(m.skills), 'agent skills')
            select(config.preloaded_skills, enabled, 'preloaded skills')
            if not set(config.skill_variants) <= enabled:
                raise ValueError(f'Variant selected for unavailable skill: {agent}')
            if not config.languages or len(config.languages) != len(set(config.languages)):
                raise ValueError(f'Invalid languages: {agent}')
            for variant in config.skill_variants.values():
                if not re.fullmatch(r'[a-z][a-z0-9_]*', variant):
                    raise ValueError(f'Invalid variant: {variant}')
            for language in config.languages:
                if not re.fullmatch('[a-z]{2,3}', language):
                    raise ValueError(f'Invalid language: {language}')
                if not self.path(f'{config.prompt_dir}/agrinet_{language}.md').is_file():
                    raise ValueError(f'Missing global prompt: {agent}/{language}')
                for skill_id in enabled:
                    self.document(skill_id, language, agent)
        for name, config in m.environments.items():
            allowed = select(config.allowed_skills, set(m.skills), 'environment skills')
            tools = select(config.allowed_tools, self.tools, 'environment tools')
            if config.allowed_skills != 'all':
                for skill_id in allowed:
                    missing = set(m.skills[skill_id].tools) - tools
                    if missing:
                        raise ValueError(f'{name}/{skill_id} requires denied tools: {sorted(missing)}')

    def validate_tools(self, tools: list) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)) or set(names) != self.tools:
            raise ValueError(f'Tool registry mismatch: unbound={sorted(set(names)-self.tools)}, '
                             f'unregistered={sorted(self.tools-set(names))}; duplicate registration is forbidden')


@lru_cache(maxsize=1)
def get_catalog() -> SkillCatalog:
    return SkillCatalog()
