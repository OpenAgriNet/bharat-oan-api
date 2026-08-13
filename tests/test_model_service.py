import asyncio
from pathlib import Path

import pytest

from agents.model_registry import ModelRegistry
from agents.model_service import ModelService

PROJECT_MODELS_PATH = Path(__file__).parent.parent / "config" / "models.yaml"


class FakeRegistry:
    def __init__(self, *, timeout: float = 1.0) -> None:
        self.timeout = timeout
        self.models = {"primary": object(), "fallback": object()}

    def get_default_alias(self, _use_case: str) -> str:
        return "primary"

    def get_fallback_chain(self, _use_case: str, primary: str) -> list[str]:
        return [primary, "fallback"]

    def get_timeout(self, _use_case: str) -> float:
        return self.timeout

    def get_model(self, alias: str):
        return self.models[alias]

    def get_model_name(self, alias: str) -> str:
        return f"model-{alias}"


def _write_registry_config(path: Path, fallbacks: str) -> None:
    path.write_text(
        f"""
models:
  primary:
    kind: vllm
    model_name: primary
    base_url: http://primary/v1
  fallback:
    kind: vllm
    model_name: fallback
    base_url: http://fallback/v1
use_cases:
  chat:
    default_alias: primary
    aliases: [primary]
    timeout_seconds: 10
    fallbacks:
{fallbacks}
"""
    )


def test_registry_expands_fallbacks_once_even_with_a_cycle(tmp_path: Path) -> None:
    config = tmp_path / "models.yaml"
    _write_registry_config(
        config,
        "      primary: [fallback]\n      fallback: [primary]",
    )
    registry = ModelRegistry(config)

    registry.validate_use_case("chat")

    assert registry.get_fallback_chain("chat", "primary") == ["primary", "fallback"]
    assert registry.get_use_case_model_aliases("chat") == ["primary", "fallback"]


def test_registry_rejects_unknown_fallback_alias(tmp_path: Path) -> None:
    config = tmp_path / "models.yaml"
    _write_registry_config(config, "      primary: [missing]")
    registry = ModelRegistry(config)

    with pytest.raises(ValueError, match="unknown target alias 'missing'"):
        registry.validate_use_case("chat")


def test_project_config_has_unified_fallbacks_for_chat_and_moderation() -> None:
    registry = ModelRegistry(PROJECT_MODELS_PATH)

    registry.validate_use_case("agrinet")
    registry.validate_use_case("moderation")

    assert registry.get_fallback_chain("agrinet", "gemma_vllm") == [
        "gemma_vllm",
        "azure_gpt41",
    ]
    assert registry.get_fallback_chain("agrinet", "azure_gpt41") == [
        "azure_gpt41",
        "gemma_vllm",
    ]
    assert registry.get_fallback_chain("moderation", "azure_gpt41") == [
        "azure_gpt41",
        "gemma_vllm",
    ]


def test_service_retries_on_configured_fallback() -> None:
    registry = FakeRegistry()
    service = ModelService(registry)  # type: ignore[arg-type]
    calls: list[str] = []

    async def runner(alias: str, _model):
        calls.append(alias)
        if alias == "primary":
            raise RuntimeError("primary unavailable")
        return "ok"

    result = asyncio.run(service.run("chat", runner))

    assert result.value == "ok"
    assert result.alias == "fallback"
    assert result.fallback_used is True
    assert result.fallback_from == "primary"
    assert result.attempted_aliases == ("primary", "fallback")
    assert calls == ["primary", "fallback"]


def test_service_applies_timeout_then_falls_back() -> None:
    registry = FakeRegistry(timeout=0.01)
    service = ModelService(registry)  # type: ignore[arg-type]

    async def runner(alias: str, _model):
        if alias == "primary":
            await asyncio.sleep(0.1)
        return alias

    result = asyncio.run(service.run("chat", runner))

    assert result.value == "fallback"
    assert result.attempted_aliases == ("primary", "fallback")


def test_service_does_not_retry_when_streaming_guard_closes() -> None:
    registry = FakeRegistry()
    service = ModelService(registry)  # type: ignore[arg-type]

    async def runner(_alias: str, _model):
        raise RuntimeError("stream already emitted")

    with pytest.raises(RuntimeError, match="stream already emitted"):
        asyncio.run(
            service.run(
                "chat",
                runner,
                can_fallback=lambda: False,
            )
        )
