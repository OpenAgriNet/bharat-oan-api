"""LLM model configuration for chat (agrinet) and moderation (safeguard) agents."""

import os

from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

load_dotenv()

# ---------------------------------------------------------------------------
# Config (env)
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4.1-nano")
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL")
VLLM_SAFEGUARD_BASE_URL = os.getenv("VLLM_SAFEGUARD_BASE_URL")
MODERATION_MODEL_NAME = os.getenv("MODERATION_MODEL_NAME", "openai/gpt-oss-safeguard-20b")

VLLM_API_KEY = "not-needed"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _openai_provider(api_key: str, base_url: str | None = None) -> OpenAIProvider:
    """Build OpenAIProvider with optional base_url (for vLLM)."""
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIProvider(**kwargs)


def _vllm_chat_model(model_name: str, base_url: str) -> OpenAIChatModel:
    """Build OpenAIChatModel for a vLLM endpoint."""
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=VLLM_API_KEY),
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

if LLM_PROVIDER == "openai":
    _provider = _openai_provider(api_key=os.getenv("OPENAI_API_KEY", ""))
    LLM_MODEL = OpenAIChatModel(LLM_MODEL_NAME, provider=_provider)
    MODERATION_LLM_MODEL = LLM_MODEL

elif LLM_PROVIDER == "vllm":
    if not VLLM_BASE_URL:
        raise ValueError("VLLM_BASE_URL must be set when LLM_PROVIDER=vllm")
    LLM_MODEL = _vllm_chat_model(LLM_MODEL_NAME, VLLM_BASE_URL)
    MODERATION_LLM_MODEL = (
        _vllm_chat_model(MODERATION_MODEL_NAME, VLLM_SAFEGUARD_BASE_URL)
        if VLLM_SAFEGUARD_BASE_URL
        else LLM_MODEL
    )

else:
    raise ValueError(
        f"Invalid LLM_PROVIDER: {LLM_PROVIDER}. Must be one of: 'openai', 'vllm'"
    )
