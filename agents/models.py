import os
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

load_dotenv()


# Get configurations from environment variables
LLM_PROVIDER   = os.getenv('LLM_PROVIDER', 'openai').lower()
LLM_MODEL_NAME  = os.getenv('LLM_MODEL_NAME')

if LLM_PROVIDER == 'vllm':
    agrinet_model_name = os.getenv('LLM_AGRINET_MODEL_NAME', 'agrinet-model')
    moderation_model_name = os.getenv('LLM_MODERATION_MODEL_NAME', 'moderation-model')
    vllm_agrinet_url = os.getenv('VLLM_AGRINET_MODEL_URL')
    vllm_moderation_url = os.getenv('VLLM_MODERATION_MODEL_URL', vllm_agrinet_url)

    if not vllm_agrinet_url:
        raise ValueError("VLLM_AGRINET_MODEL_URL is required when using vllm provider")

    AGRINET_MODEL = OpenAIChatModel(
        agrinet_model_name,
        provider=OpenAIProvider(
            base_url=vllm_agrinet_url, 
            api_key="not-needed"  
        ),
    )
    MODERATION_MODEL = OpenAIChatModel(
        moderation_model_name,
        provider=OpenAIProvider(
            base_url=vllm_moderation_url, 
            api_key="not-needed"  
        ),
    )
elif LLM_PROVIDER == 'openai':
    AGRINET_MODEL = OpenAIChatModel(
        os.getenv('LLM_AGRINET_MODEL_NAME', 'gpt-3.5-turbo'),
        provider=OpenAIProvider(
            api_key=os.getenv('OPENAI_API_KEY'),
        ),
    )
    MODERATION_MODEL = OpenAIChatModel(
        os.getenv('LLM_MODERATION_MODEL_NAME', 'gpt-3.5-turbo'),
        provider=OpenAIProvider(
            api_key=os.getenv('OPENAI_API_KEY'),
        ),
    )
elif LLM_PROVIDER == 'azure-openai':
    azure_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
    azure_api_key = os.getenv('AZURE_OPENAI_API_KEY')
    azure_api_version = os.getenv('AZURE_OPENAI_API_VERSION')
    azure_deployment_name = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME')
    
    if not azure_endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT environment variable is required")
    if not azure_api_key:
        raise ValueError("AZURE_OPENAI_API_KEY environment variable is required")
    if not azure_api_version:
        raise ValueError("AZURE_OPENAI_API_VERSION environment variable is required")
    if not azure_deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME environment variable is required")
    
    # Allow overriding the deployment name for specific agents if needed
    agrinet_deployment = os.getenv('LLM_AGRINET_MODEL_NAME', azure_deployment_name)
    moderation_deployment = os.getenv('LLM_MODERATION_MODEL_NAME', azure_deployment_name)

    azure_client = AsyncAzureOpenAI(
        azure_endpoint=azure_endpoint.rstrip('/'),
        api_version=azure_api_version,
        api_key=azure_api_key,
    )
    
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
    raise ValueError(f"Invalid LLM_PROVIDER: {LLM_PROVIDER}. Must be one of: 'vllm', 'openai', 'azure-openai'")