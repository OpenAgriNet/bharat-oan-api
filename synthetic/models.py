import os
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from dotenv import load_dotenv

load_dotenv()


# Get configurations from environment variables
LLM_PROVIDER    = os.getenv('LLM_PROVIDER', 'openai').lower()
LLM_MODEL_NAME = os.getenv('LLM_MODEL_NAME')

if LLM_PROVIDER == 'vllm':
    LLM_MODEL = OpenAIChatModel(
        LLM_MODEL_NAME,
        provider=OpenAIProvider(
            base_url=os.getenv('VLLM_BASE_URL'), 
            api_key="not-required",
        ),
    )

elif LLM_PROVIDER == 'openai':
    LLM_MODEL = OpenAIResponsesModel(
        LLM_MODEL_NAME,
        provider=OpenAIProvider(
            api_key=os.getenv('OPENAI_API_KEY'),
        ),
    )

elif LLM_PROVIDER == 'anthropic':
    LLM_MODEL = AnthropicModel(
        LLM_MODEL_NAME,
        provider=AnthropicProvider(
            api_key=os.getenv('ANTHROPIC_API_KEY'),
        ),
    )