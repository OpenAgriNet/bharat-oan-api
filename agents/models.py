"""Initialize every agent from the same model registry."""
from pydantic_ai import Agent

from agents.model_registry import get_registry


def configured_agent(use_case: str, **kwargs) -> Agent:
    registry = get_registry()
    return Agent(model=registry.get_model(registry.get_default_alias(use_case)), **kwargs)
