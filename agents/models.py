import json
import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel

import agents.providers as providers
from app.config import settings

load_dotenv()

AgrinetRoute = Literal["gpt41", "gemma"]
AGRINET_DEFAULT_ROUTE: AgrinetRoute = "gpt41"


def _model_var(alias: str, suffix: str) -> str:
    return f"MODEL_{alias}_{suffix}"


def _require(alias: str, suffix: str) -> str:
    var = _model_var(alias, suffix)
    value = os.getenv(var)
    if not value:
        raise ValueError(f"{var} is required for model '{alias}'")
    return value


@lru_cache(maxsize=None)
def get_model(alias: str) -> tuple[OpenAIChatModel, dict]:
    """Look up a named model from the registry. MODEL_{alias}_KIND selects the
    provider kind; every other setting for that model lives under its own
    MODEL_{alias}_* var -- fully self-contained, never inherited from another
    alias or role. Built once per alias (cached) so two use cases naming the
    same alias share one client/connection pool."""
    kind = _require(alias, "KIND").strip().lower()

    if kind == "openai":
        return providers.openai_compatible_model(
            _require(alias, "MODEL_NAME"),
            api_key=_require(alias, "API_KEY"),
        )

    if kind == "vllm":
        return providers.openai_compatible_model(
            _require(alias, "MODEL_NAME"),
            base_url=_require(alias, "BASE_URL"),
            api_key="not-needed",
        )

    if kind == "external":
        disable_streaming = os.getenv(_model_var(alias, "DISABLE_STREAMING"), "").strip().lower() in ("1", "true", "yes", "on")
        return providers.openai_compatible_model(
            _require(alias, "MODEL_NAME"),
            base_url=_require(alias, "BASE_URL"),
            api_key=os.getenv(_model_var(alias, "API_KEY"), "not-needed"),
            extra_headers=json.loads(os.getenv(_model_var(alias, "EXTRA_HEADERS_JSON"), "{}") or "{}"),
            extra_body=json.loads(os.getenv(_model_var(alias, "EXTRA_BODY_JSON"), "{}") or "{}"),
            disable_streaming=disable_streaming,
        )

    if kind == "azure-openai":
        return providers.azure_openai_model(
            _require(alias, "DEPLOYMENT_NAME"),
            endpoint=_require(alias, "ENDPOINT"),
            api_key=_require(alias, "API_KEY"),
            api_version=_require(alias, "API_VERSION"),
        )

    raise ValueError(f"Invalid {_model_var(alias, 'KIND')}: {kind!r}. Must be one of: openai, vllm, external, azure-openai")


def _use_case_model(env_var: str, default_alias: str) -> tuple[OpenAIChatModel, dict]:
    return get_model(os.getenv(env_var, default_alias))


LLM_AGRINET_MODEL, AGRINET_EXTRA_MODEL_SETTINGS = _use_case_model("AGRINET_MODEL_ALIAS", "GPT41")
MODERATION_MODEL, MODERATION_EXTRA_MODEL_SETTINGS = _use_case_model("MODERATION_MODEL_ALIAS", "GPT41")


def get_agrinet_route_model(route: AgrinetRoute) -> OpenAIChatModel:
    if route == "gpt41":
        return LLM_AGRINET_MODEL
    if route == "gemma":
        return get_model(os.getenv("AGRINET_ROUTE_GEMMA_MODEL_ALIAS", "GEMMA"))[0]
    raise ValueError(f"Unsupported agrinet route: {route}")


def get_agrinet_route_model_name(route: AgrinetRoute) -> str:
    return get_agrinet_route_model(route).model_name


def validate_agrinet_routing_config() -> None:
    if not settings.agrinet_routing_enabled:
        return

    weights = (
        settings.agrinet_route_gpt41_weight,
        settings.agrinet_route_gemma_weight,
    )
    if any(weight < 0 for weight in weights):
        raise ValueError("AGRINET routing weights must be non-negative integers")
    if sum(weights) != 100:
        raise ValueError("AGRINET routing weights must sum to 100")
    if settings.agrinet_route_ttl_seconds <= 0:
        raise ValueError("AGRINET_ROUTE_TTL_SECONDS must be a positive integer")

    # Force Gemma config validation during startup when routing is enabled.
    get_agrinet_route_model("gemma")


# Langfuse generation `model` field (providedModelName) for dashboard model breakdown.
LANGFUSE_AGRINET_MODEL_NAME = LLM_AGRINET_MODEL.model_name
LANGFUSE_MODERATION_MODEL_NAME = MODERATION_MODEL.model_name
