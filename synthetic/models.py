import os
#from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
# from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from dotenv import load_dotenv

load_dotenv()


# Get configurations from environment variables
LLM_AGRINET_MODEL_NAME = os.getenv('LLM_AGRINET_MODEL_NAME')
LLM_MODERATION_MODEL_NAME = os.getenv('LLM_MODERATION_MODEL_NAME')
ENABLE_INSTRUMENTATION = os.getenv('ENABLE_INSTRUMENTATION', 'false').lower() in ('true', '1', 'yes')
VLLM_AGRINET_MODEL_URL = os.getenv('VLLM_AGRINET_MODEL_URL')
VLLM_MODERATION_MODEL_URL = os.getenv('VLLM_MODERATION_MODEL_URL')

LLM_MODEL = OpenAIChatModel(
    LLM_AGRINET_MODEL_NAME,
    provider=OpenAIProvider(
        base_url=VLLM_AGRINET_MODEL_URL,
        api_key="not-required",
    ),
)

LLM_MODERATION_MODEL = OpenAIChatModel(
    LLM_MODERATION_MODEL_NAME,
    provider=OpenAIProvider(
        base_url=VLLM_MODERATION_MODEL_URL,
        api_key="not-required",
    ),
)

# Candidate model for arena comparison
VLLM_AGRINET_CANDIDATE_MODEL_URL = os.getenv('VLLM_AGRINET_CANDIDATE_MODEL_URL')
LLM_AGRINET_CANDIDATE_MODEL_NAME = os.getenv('LLM_AGRINET_CANDIDATE_MODEL_NAME')

LLM_CANDIDATE_MODEL = OpenAIChatModel(
    LLM_AGRINET_CANDIDATE_MODEL_NAME,
    provider=OpenAIProvider(
        base_url=VLLM_AGRINET_CANDIDATE_MODEL_URL,
        api_key="not-required",
    ),
) if VLLM_AGRINET_CANDIDATE_MODEL_URL else None