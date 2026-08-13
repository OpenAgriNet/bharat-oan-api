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


def _require(alias: str, config: dict, key: str) -> str:
    value = (config.get(key) or "").strip()
    if not value:
        raise ValueError(f"Alias '{alias}': {key} is required")
    return value


def _build_openai(alias: str, config: dict) -> OpenAIChatModel:
    api_key = _require(alias, config, "api_key")
    return OpenAIChatModel(
        config["model_name"],
        provider=OpenAIProvider(api_key=api_key),
    )


def _build_vllm(alias: str, config: dict) -> OpenAIChatModel:
    base_url = _require(alias, config, "base_url")
    model_name = _require(alias, config, "model_name")
    api_key = (config.get("api_key") or "not-needed").strip() or "not-needed"
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )


def _build_azure(alias: str, config: dict) -> OpenAIChatModel:
    endpoint = _require(alias, config, "endpoint")
    api_key = _require(alias, config, "api_key")
    api_version = _require(alias, config, "api_version")
    deployment_name = _require(alias, config, "deployment_name")
    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint.rstrip("/"),
        api_version=api_version,
        api_key=api_key,
    )
    return OpenAIChatModel(
        deployment_name,
        provider=OpenAIProvider(openai_client=client),
    )


_BUILDERS = {
    "openai": _build_openai,
    "vllm": _build_vllm,
    "bharat_ai_grid": _build_vllm,
    "azure-openai": _build_azure,
}


def _build_model(alias: str, config: dict) -> OpenAIChatModel:
    kind = config.get("kind", "")
    builder = _BUILDERS.get(kind)
    if not builder:
        raise ValueError(f"Alias '{alias}': unknown kind '{kind}'")
    return builder(alias, config)


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

    def get_use_cases(self) -> list[str]:
        return list(self._use_cases_cfg)

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

    # --- execution fallback policy ---

    def get_fallback_aliases(self, use_case: str, alias: str) -> list[str]:
        fallbacks = self._use_cases_cfg.get(use_case, {}).get("fallbacks", {})
        return list(fallbacks.get(alias, []))

    def get_fallback_chain(self, use_case: str, primary_alias: str) -> list[str]:
        """Return an ordered, de-duplicated fallback chain for one execution.

        The breadth-first expansion supports multi-level policies while making
        cycles harmless: a model alias is attempted at most once per request.
        """
        chain: list[str] = []
        queue = [primary_alias]
        while queue:
            alias = queue.pop(0)
            if alias in chain:
                continue
            chain.append(alias)
            queue.extend(self.get_fallback_aliases(use_case, alias))
        return chain

    def get_use_case_model_aliases(self, use_case: str) -> list[str]:
        aliases: list[str] = []
        primaries = [
            self.get_default_alias(use_case),
            *self.get_use_case_aliases(use_case),
        ]
        for primary in primaries:
            if not primary:
                continue
            for alias in self.get_fallback_chain(use_case, primary):
                if alias not in aliases:
                    aliases.append(alias)
        return aliases

    # --- model capacity policy ---

    def get_max_concurrency(self, alias: str) -> int | None:
        raw = self._models_cfg.get(alias, {}).get("capacity", {}).get("max_concurrency")
        return int(raw) if raw is not None else None

    def get_metrics_cache_ttl(self, alias: str) -> int:
        raw = self._models_cfg.get(alias, {}).get("capacity", {}).get("metrics_cache_ttl", 2)
        return int(raw)

    def get_metrics_url(self, alias: str) -> str | None:
        config = self._models_cfg.get(alias, {})
        override = (config.get("capacity", {}).get("metrics_url") or "").strip()
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

        timeout = self.get_timeout(use_case)
        if timeout <= 0:
            raise ValueError(f"Use case '{use_case}': timeout_seconds must be positive")

        fallbacks = uc.get("fallbacks", {})
        if not isinstance(fallbacks, dict):
            raise ValueError(f"Use case '{use_case}': fallbacks must be a mapping")
        for source, targets in fallbacks.items():
            if source not in self._models_cfg:
                raise ValueError(
                    f"Use case '{use_case}' fallback references unknown source alias '{source}'"
                )
            if not isinstance(targets, list):
                raise ValueError(
                    f"Use case '{use_case}' fallbacks for '{source}' must be a list"
                )
            for target in targets:
                if target not in self._models_cfg:
                    raise ValueError(
                        f"Use case '{use_case}' fallback references unknown target alias '{target}'"
                    )


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    return ModelRegistry()
