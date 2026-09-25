from __future__ import annotations

import os
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from httpx import TransportError
from openai import APIConnectionError, AsyncAzureOpenAI
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

_YAML_PATH = Path(__file__).parent.parent / "config" / "models.yaml"
_ENV_RE = re.compile(r"\$\{([^}]+)\}")
_ERROR_TYPES = {
    "ModelAPIError": ModelAPIError,
    "ModelHTTPError": ModelHTTPError,
    "TimeoutError": TimeoutError,
    "APIConnectionError": APIConnectionError,
    "TransportError": TransportError,
    "UnexpectedModelBehavior": UnexpectedModelBehavior,
}


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


def _build_openai_compatible(alias: str, config: dict, api_key: str) -> Model:
    model_name = _require(alias, config, "model_name")
    provider_args = {"api_key": api_key}
    if base_url := (config.get("base_url") or "").strip():
        provider_args["base_url"] = base_url.rstrip("/") + "/"
    api = config.get("api", "chat")
    if api == "chat":
        model_class = OpenAIChatModel
    elif api == "responses":
        model_class = OpenAIResponsesModel
    else:
        raise ValueError(f"Alias '{alias}': unsupported API '{api}'")
    return model_class(model_name, provider=OpenAIProvider(**provider_args), settings=config.get("settings"))


def _build_openai(alias: str, config: dict) -> Model:
    return _build_openai_compatible(alias, config, _require(alias, config, "api_key"))


def _build_vllm(alias: str, config: dict) -> OpenAIChatModel:
    base_url = _require(alias, config, "base_url")
    model_name = _require(alias, config, "model_name")
    api_key = (config.get("api_key") or "not-needed").strip() or "not-needed"
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        settings=config.get("settings"),
    )


def _build_azure(alias: str, config: dict) -> Model:
    if config.get("base_url"):
        return _build_openai_compatible(alias, config, _require(alias, config, "api_key"))
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
        settings=config.get("settings"),
    )


_BUILDERS = {
    "openai": _build_openai,
    "vllm": _build_vllm,
    "azure-openai": _build_azure,
}


def _build_model(alias: str, config: dict) -> Model:
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
        self._model_cache: dict[str, Model] = {}

    # --- model access ---

    def get_model(self, alias: str) -> Model:
        if alias not in self._model_cache:
            if alias not in self._models_cfg:
                raise ValueError(f"Model alias '{alias}' not found in config/models.yaml")
            self._model_cache[alias] = _build_model(alias, self._models_cfg[alias])
        return self._model_cache[alias]

    def get_model_name(self, alias: str) -> str:
        config = self._models_cfg.get(alias, {})
        if config.get("kind") == "azure-openai":
            return config.get("deployment_name") or config.get("model_name", alias)
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
        if not n:
            raise ValueError(f"Use case {use_case!r} has no aliases")
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

    def routing_enabled(self, use_case: str) -> bool:
        value = self._use_cases_cfg[use_case].get("routing_enabled", True)
        if isinstance(value, bool):
            return value
        if value.lower() not in ("", "false", "0", "no", "off", "true", "1", "yes", "on"):
            raise ValueError(f"Use case '{use_case}': invalid routing_enabled")
        return value.lower() in ("true", "1", "yes", "on")

    def get_reachable_aliases(self, use_case: str) -> set[str]:
        reachable = set()
        for alias in [*self.get_use_case_aliases(use_case), self.get_default_alias(use_case)]:
            chain = set()
            while alias:
                if alias not in self._models_cfg:
                    raise ValueError(f"Unknown model alias '{alias}' in use case '{use_case}'")
                if alias in chain:
                    raise ValueError(f"Fallback cycle at model alias '{alias}'")
                chain.add(alias)
                reachable.add(alias)
                alias = self.get_fallback_alias(alias)
        return reachable

    def get_fallback_errors(self, alias: str) -> tuple[type[Exception], ...]:
        names = self._models_cfg[alias].get("fallback", {}).get("on_error", [])
        try:
            return tuple(_ERROR_TYPES[name] for name in names)
        except KeyError as exc:
            raise ValueError(f"Alias '{alias}': unknown fallback error {exc}") from exc

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

        if not math.isfinite(self.get_timeout(use_case)) or self.get_timeout(use_case) <= 0:
            raise ValueError(f"Use case '{use_case}': timeout_seconds must be positive and finite")
        self.routing_enabled(use_case)
        for alias in self.get_reachable_aliases(use_case):
            config = self._models_cfg[alias]
            if config.get("kind") not in _BUILDERS:
                raise ValueError(f"Alias '{alias}': unknown provider kind")
            fallback = config.get("fallback", {})
            errors = self.get_fallback_errors(alias)
            threshold = self.get_fallback_on_concurrency(alias)
            if fallback and (not fallback.get("to") or not (errors or threshold is not None)):
                raise ValueError(f"Alias '{alias}': fallback requires a target and a trigger")
            if threshold is not None:
                if config["kind"] != "vllm" or threshold < 0:
                    raise ValueError(f"Alias '{alias}': capacity fallback requires vllm and a nonnegative threshold")
                if self.get_metrics_cache_ttl(alias) <= 0:
                    raise ValueError(f"Alias '{alias}': metrics_cache_ttl must be positive")

    def validate(self) -> None:
        """Validate every use case and build reachable clients without making requests."""
        for use_case in self._use_cases_cfg:
            self.validate_use_case(use_case)
            for alias in self.get_reachable_aliases(use_case):
                self.get_model(alias)


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    return ModelRegistry()
