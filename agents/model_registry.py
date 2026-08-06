from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncAzureOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

_YAML_PATH = Path(__file__).parent.parent / "config" / "models.yaml"
_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env(value: str) -> str:
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _resolve_values(obj: Any) -> Any:
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _resolve_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_values(v) for v in obj]
    return obj


def _build_model(alias: str, config: dict) -> OpenAIChatModel:
    kind = config.get("kind", "")
    if kind == "openai":
        api_key = config.get("api_key", "")
        if not api_key:
            raise ValueError(f"Alias '{alias}': api_key is required for kind=openai")
        return OpenAIChatModel(
            config["model_name"],
            provider=OpenAIProvider(api_key=api_key),
        )

    if kind in ("vllm", "bharat_ai_grid"):
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ValueError(f"Alias '{alias}': base_url is required for kind={kind}")
        model_name = (config.get("model_name") or "").strip()
        if not model_name:
            raise ValueError(f"Alias '{alias}': model_name is required for kind={kind}")
        api_key = (config.get("api_key") or "not-needed").strip() or "not-needed"
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )

    if kind == "azure-openai":
        endpoint = (config.get("endpoint") or "").strip()
        api_key = (config.get("api_key") or "").strip()
        api_version = (config.get("api_version") or "").strip()
        deployment_name = (config.get("deployment_name") or "").strip()
        if not endpoint:
            raise ValueError(f"Alias '{alias}': endpoint is required for kind=azure-openai")
        if not api_key:
            raise ValueError(f"Alias '{alias}': api_key is required for kind=azure-openai")
        if not api_version:
            raise ValueError(f"Alias '{alias}': api_version is required for kind=azure-openai")
        if not deployment_name:
            raise ValueError(f"Alias '{alias}': deployment_name is required for kind=azure-openai")
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint.rstrip("/"),
            api_version=api_version,
            api_key=api_key,
        )
        return OpenAIChatModel(
            deployment_name,
            provider=OpenAIProvider(openai_client=client),
        )

    raise ValueError(f"Alias '{alias}': unknown kind '{kind}'")


class ModelRegistry:
    def __init__(self, path: Path = _YAML_PATH) -> None:
        raw = yaml.safe_load(path.read_text())
        resolved = _resolve_values(raw)
        self._models_cfg: dict[str, dict] = resolved.get("models", {})
        self._use_cases_cfg: dict[str, dict] = resolved.get("use_cases", {})
        self._model_cache: dict[str, OpenAIChatModel] = {}

    # --- model access ---

    def get_model(self, alias: str) -> OpenAIChatModel:
        if alias not in self._model_cache:
            if alias not in self._models_cfg:
                raise ValueError(f"Model alias '{alias}' not found in config/models.yaml")
            self._model_cache[alias] = _build_model(alias, self._models_cfg[alias])
        return self._model_cache[alias]

    def get_model_name(self, alias: str) -> str:
        config = self._models_cfg.get(alias, {})
        if config.get("kind") == "azure-openai":
            return config.get("deployment_name", alias)
        return config.get("model_name", alias)

    # --- use-case access ---

    def get_use_case_aliases(self, use_case: str) -> list[str]:
        return list(self._use_cases_cfg.get(use_case, {}).get("aliases", []))

    def get_use_case_proportions(self, use_case: str) -> list[int]:
        uc = self._use_cases_cfg.get(use_case, {})
        aliases = uc.get("aliases", [])
        proportions = uc.get("proportions")
        if proportions is not None:
            return [int(p) for p in proportions]
        n = len(aliases)
        base = 100 // n
        remainder = 100 - base * n
        return [base + (1 if i < remainder else 0) for i in range(n)]

    def get_default_alias(self, use_case: str) -> str:
        uc = self._use_cases_cfg.get(use_case, {})
        explicit = uc.get("default_alias", "")
        if explicit:
            return explicit
        aliases = uc.get("aliases", [])
        return aliases[0] if aliases else ""

    def get_routing_ttl(self, use_case: str) -> int:
        return int(self._use_cases_cfg.get(use_case, {}).get("routing_ttl_seconds", 7200))

    def get_timeout(self, use_case: str) -> float:
        return float(self._use_cases_cfg.get(use_case, {}).get("timeout_seconds", 45))

    # --- fallback / capacity ---

    def get_fallback_alias(self, alias: str) -> str | None:
        return self._models_cfg.get(alias, {}).get("fallback", {}).get("to")

    def get_fallback_on_concurrency(self, alias: str) -> int | None:
        raw = self._models_cfg.get(alias, {}).get("fallback", {}).get("on_concurrency_above")
        return int(raw) if raw is not None else None

    def get_metrics_cache_ttl(self, alias: str) -> int:
        raw = self._models_cfg.get(alias, {}).get("fallback", {}).get("metrics_cache_ttl", 2)
        return int(raw)

    def get_metrics_url(self, alias: str) -> str | None:
        config = self._models_cfg.get(alias, {})
        override = (config.get("fallback", {}).get("metrics_url") or "").strip()
        if override:
            return override
        base_url = (config.get("base_url") or "").strip()
        if base_url:
            return re.sub(r"/v1/?$", "", base_url) + "/metrics"
        return None

    # --- startup validation ---

    def validate_use_case(self, use_case: str) -> None:
        uc = self._use_cases_cfg.get(use_case, {})
        aliases = uc.get("aliases", [])
        if not aliases:
            raise ValueError(f"Use case '{use_case}' has no aliases in config/models.yaml")

        for alias in aliases:
            if alias not in self._models_cfg:
                raise ValueError(
                    f"Use case '{use_case}' references unknown alias '{alias}'"
                )

        proportions = uc.get("proportions")
        if proportions is not None:
            proportions = [int(p) for p in proportions]
            if len(proportions) != len(aliases):
                raise ValueError(
                    f"Use case '{use_case}': proportions length ({len(proportions)}) "
                    f"must match aliases length ({len(aliases)})"
                )
            if any(p < 0 for p in proportions):
                raise ValueError(f"Use case '{use_case}': proportions must be non-negative")
            if sum(proportions) != 100:
                raise ValueError(f"Use case '{use_case}': proportions must sum to 100")

        ttl = self.get_routing_ttl(use_case)
        if ttl <= 0:
            raise ValueError(f"Use case '{use_case}': routing_ttl_seconds must be positive")

        default = self.get_default_alias(use_case)
        if default and default not in self._models_cfg:
            raise ValueError(
                f"Use case '{use_case}': default_alias '{default}' not found in models"
            )


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    return ModelRegistry()
