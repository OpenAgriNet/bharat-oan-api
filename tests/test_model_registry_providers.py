from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from agents.model_registry import ModelRegistry


def test_provider_kinds_build_configured_models(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEY", "test-key")
    path = tmp_path / "models.yaml"
    path.write_text(
        """
models:
  azure_classic:
    kind: azure-openai
    deployment_name: gpt-4.1
    endpoint: https://classic.invalid
    api_key: ${KEY}
    api_version: "2025-01-01-preview"
  azure_luna:
    kind: azure-openai
    api: responses
    model_name: gpt-5.6-luna
    settings:
      openai_reasoning_effort: none
    base_url: https://luna.invalid/openai/v1
    api_key: ${KEY}
  openai_grid:
    kind: openai
    model_name: grid-model
    base_url: https://grid.invalid/v1
    api_key: ${KEY}
  local_gemma:
    kind: vllm
    model_name: gemma
    base_url: http://localhost:8867/v1
    api_key: ""
    fallback:
      to: azure_luna
      on_error: [ModelAPIError, TimeoutError]
use_cases: {}
"""
    )

    registry = ModelRegistry(path)

    assert isinstance(registry.get_model("azure_classic"), OpenAIChatModel)
    assert isinstance(registry.get_model("azure_luna"), OpenAIResponsesModel)
    assert isinstance(registry.get_model("openai_grid"), OpenAIChatModel)
    assert isinstance(registry.get_model("local_gemma"), OpenAIChatModel)
    assert registry.get_model("azure_luna").settings["openai_reasoning_effort"] == "none"
    assert registry.get_model_name("azure_luna") == "gpt-5.6-luna"


def test_chat_routes_70_30_and_falls_back_to_mini(monkeypatch):
    monkeypatch.setenv("AGRINET_PROPORTION_GEMMA_VLLM", "70")
    monkeypatch.setenv("AGRINET_PROPORTION_AZURE_GPT54_MINI", "30")
    registry = ModelRegistry()

    assert registry.get_use_case_aliases("agrinet") == [
        "gemma_vllm",
        "azure_gpt54_mini",
    ]
    assert registry.get_use_case_proportions("agrinet") == [70, 30]
    assert registry.get_default_alias("agrinet") == "azure_gpt54_mini"
    assert registry.get_fallback_alias("gemma_vllm") == "azure_gpt54_mini"
    assert registry.get_fallback_alias("bharat_ai_grid_gemma") == "azure_gpt54_mini"
    assert registry.get_fallback_alias("azure_gpt41") == "azure_gpt54_mini"
    assert registry.get_fallback_alias("azure_gpt56_luna") == "azure_gpt54_mini"


def test_moderation_uses_gemma_with_mini_fallback():
    registry = ModelRegistry()

    assert registry.get_use_case_aliases("moderation") == ["gemma_vllm"]
    assert registry.get_default_alias("moderation") == "gemma_vllm"
    assert registry.get_fallback_alias("gemma_vllm") == "azure_gpt54_mini"
