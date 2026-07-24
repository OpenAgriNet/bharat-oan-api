import json
import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

import agents.providers as providers
from app.config import settings

load_dotenv()

AgrinetRoute = Literal["gpt41", "gemma"]
AGRINET_DEFAULT_ROUTE: AgrinetRoute = "gpt41"


def _role_value(base_var: str, moderation_var: str, role: str, default: str | None = None) -> str | None:
    """AGRINET always uses `base_var`. MODERATION uses `moderation_var` if set,
    else falls back to the same value as AGRINET -- so a single base_var defines
    the setting for both roles at once, and moderation only needs to be
    special-cased when it should actually differ."""
    base_value = os.getenv(base_var, default)
    if role == "AGRINET":
        return base_value
    return os.getenv(moderation_var, base_value)


# One place to decide which provider kind + config values back each role
# ("AGRINET" or "MODERATION"). All the actual client-construction logic lives
# in agents/providers.py; this function only resolves env vars and dispatches.
def _resolve_role(role: str) -> tuple[OpenAIChatModel, dict]:
    if LLM_PROVIDER == "openai":
        return providers.openai_compatible_model(
            os.getenv(f"LLM_{role}_MODEL_NAME", "gpt-3.5-turbo"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    if LLM_PROVIDER == "vllm":
        base_url = _role_value("VLLM_MODEL_URL", "VLLM_MODERATION_MODEL_URL", role)
        if not base_url:
            raise ValueError("VLLM_MODEL_URL is required when using vllm provider")
        return providers.openai_compatible_model(
            os.getenv(f"LLM_{role}_MODEL_NAME", f"{role.lower()}-model"),
            base_url=base_url,
            api_key="not-needed",
        )

    if LLM_PROVIDER == "external":
        model_name = _role_value("EXTERNAL_MODEL_NAME", "EXTERNAL_MODERATION_MODEL_NAME", role)
        base_url = _role_value("EXTERNAL_BASE_URL", "EXTERNAL_MODERATION_BASE_URL", role)
        if not model_name or not base_url:
            raise ValueError("EXTERNAL_MODEL_NAME and EXTERNAL_BASE_URL are required when using external provider")
        api_key = _role_value("EXTERNAL_API_KEY", "EXTERNAL_MODERATION_API_KEY", role, default="not-needed")
        extra_headers = json.loads(
            _role_value("EXTERNAL_EXTRA_HEADERS_JSON", "EXTERNAL_MODERATION_EXTRA_HEADERS_JSON", role, default="{}") or "{}"
        )
        extra_body = json.loads(
            _role_value("EXTERNAL_EXTRA_BODY_JSON", "EXTERNAL_MODERATION_EXTRA_BODY_JSON", role, default="{}") or "{}"
        )
        disable_streaming = os.getenv("EXTERNAL_DISABLE_STREAMING", "").strip().lower() in ("1", "true", "yes", "on")
        return providers.openai_compatible_model(
            model_name,
            base_url=base_url,
            api_key=api_key,
            extra_headers=extra_headers,
            extra_body=extra_body,
            disable_streaming=disable_streaming,
        )

    if LLM_PROVIDER == "azure-openai":
        azure_deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        if not azure_deployment_name:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME environment variable is required")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
        if not azure_endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT environment variable is required")
        if not azure_api_key:
            raise ValueError("AZURE_OPENAI_API_KEY environment variable is required")
        if not azure_api_version:
            raise ValueError("AZURE_OPENAI_API_VERSION environment variable is required")
        return providers.azure_openai_model(
            os.getenv(f"LLM_{role}_MODEL_NAME", azure_deployment_name),
            endpoint=azure_endpoint,
            api_key=azure_api_key,
            api_version=azure_api_version,
        )

    raise ValueError(
        f"Invalid LLM_PROVIDER: {LLM_PROVIDER}. Must be one of: 'openai', 'vllm', 'external', 'azure-openai'"
    )


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_AGRINET_MODEL, AGRINET_EXTRA_MODEL_SETTINGS = _resolve_role("AGRINET")
MODERATION_MODEL, MODERATION_EXTRA_MODEL_SETTINGS = _resolve_role("MODERATION")


@lru_cache(maxsize=1)
def _build_gemma_agrinet_model() -> OpenAIChatModel:
    provider = (settings.agrinet_gemma_provider or "vllm").strip().lower()
    if provider != "vllm":
        raise ValueError(
            f"Unsupported AGRINET_GEMMA_PROVIDER: {provider}. Only 'vllm' is supported."
        )

    model_name = (settings.agrinet_gemma_model_name or "").strip()
    if not model_name:
        raise ValueError("AGRINET_GEMMA_MODEL_NAME environment variable is required")

    base_url = (settings.agrinet_gemma_base_url or "").strip()
    if not base_url:
        raise ValueError("AGRINET_GEMMA_BASE_URL environment variable is required")

    api_key = (settings.agrinet_gemma_api_key or "not-needed").strip() or "not-needed"

    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
        ),
    )


def get_agrinet_route_model(route: AgrinetRoute) -> OpenAIChatModel:
    if route == "gpt41":
        return LLM_AGRINET_MODEL
    if route == "gemma":
        return _build_gemma_agrinet_model()
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
