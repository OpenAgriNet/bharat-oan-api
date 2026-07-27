import json
import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import settings

load_dotenv()

AgrinetRoute = Literal["gpt41", "gemma"]
AGRINET_DEFAULT_ROUTE: AgrinetRoute = "gpt41"


@lru_cache(maxsize=1)
def _get_default_azure_client() -> AsyncAzureOpenAI:
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION")

    if not azure_endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT environment variable is required")
    if not azure_api_key:
        raise ValueError("AZURE_OPENAI_API_KEY environment variable is required")
    if not azure_api_version:
        raise ValueError("AZURE_OPENAI_API_VERSION environment variable is required")

    return AsyncAzureOpenAI(
        azure_endpoint=azure_endpoint.rstrip("/"),
        api_version=azure_api_version,
        api_key=azure_api_key,
    )


# Keep the primary agrinet + moderation wiring close to the original layout so
# the new routing behavior adds as little mental overhead as possible.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

if LLM_PROVIDER == "vllm":
    agrinet_model_name = os.getenv("LLM_AGRINET_MODEL_NAME", "agrinet-model")
    moderation_model_name = os.getenv("LLM_MODERATION_MODEL_NAME", "moderation-model")
    vllm_agrinet_url = os.getenv("VLLM_AGRINET_MODEL_URL")
    vllm_moderation_url = os.getenv("VLLM_MODERATION_MODEL_URL", vllm_agrinet_url)
    # Optional: only needed to point "vllm" mode at a gateway that requires
    # auth (e.g. the Bharat Grid gateway) instead of a plain self-hosted vLLM
    # server. Unset, behavior is identical to before (no api key, no headers).
    vllm_api_key = os.getenv("VLLM_API_KEY", "not-needed")
    vllm_extra_headers = json.loads(os.getenv("VLLM_EXTRA_HEADERS_JSON", "{}") or "{}")

    if not vllm_agrinet_url:
        raise ValueError("VLLM_AGRINET_MODEL_URL is required when using vllm provider")

    def _vllm_provider(base_url: str) -> OpenAIProvider:
        if vllm_extra_headers:
            client = AsyncOpenAI(base_url=base_url, api_key=vllm_api_key, default_headers=vllm_extra_headers)
            return OpenAIProvider(openai_client=client)
        return OpenAIProvider(base_url=base_url, api_key=vllm_api_key)

    AGRINET_MODEL = OpenAIChatModel(agrinet_model_name, provider=_vllm_provider(vllm_agrinet_url))
    MODERATION_MODEL = OpenAIChatModel(moderation_model_name, provider=_vllm_provider(vllm_moderation_url))
elif LLM_PROVIDER == "openai":
    AGRINET_MODEL = OpenAIChatModel(
        os.getenv("LLM_AGRINET_MODEL_NAME", "gpt-3.5-turbo"),
        provider=OpenAIProvider(
            api_key=os.getenv("OPENAI_API_KEY"),
        ),
    )
    MODERATION_MODEL = OpenAIChatModel(
        os.getenv("LLM_MODERATION_MODEL_NAME", "gpt-3.5-turbo"),
        provider=OpenAIProvider(
            api_key=os.getenv("OPENAI_API_KEY"),
        ),
    )
elif LLM_PROVIDER == "azure-openai":
    azure_deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    if not azure_deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME environment variable is required")

    agrinet_deployment = os.getenv("LLM_AGRINET_MODEL_NAME", azure_deployment_name)
    moderation_deployment = os.getenv("LLM_MODERATION_MODEL_NAME", azure_deployment_name)
    azure_client = _get_default_azure_client()

    AGRINET_MODEL = OpenAIChatModel(
        agrinet_deployment,
        provider=OpenAIProvider(openai_client=azure_client),
    )
    MODERATION_MODEL = OpenAIChatModel(
        moderation_deployment,
        provider=OpenAIProvider(
            openai_client=azure_client,
        ),
    )
else:
    raise ValueError(
        f"Invalid LLM_PROVIDER: {LLM_PROVIDER}. Must be one of: 'vllm', 'openai', 'azure-openai'"
    )


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
        return AGRINET_MODEL
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
LANGFUSE_AGRINET_MODEL_NAME = AGRINET_MODEL.model_name
LANGFUSE_MODERATION_MODEL_NAME = MODERATION_MODEL.model_name
