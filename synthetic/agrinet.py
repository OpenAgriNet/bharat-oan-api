from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt
from synthetic.models import LLM_MODEL, LLM_CANDIDATE_MODEL, ENABLE_INSTRUMENTATION
from synthetic.tools import TOOLS
from pydantic_ai.settings import ModelSettings
#from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings, OpenAIResponsesModelSettings
from synthetic.deps import FarmerContext

# Shared model settings for both agents
# Qwen
# _AGRINET_MODEL_SETTINGS = OpenAIChatModelSettings(
#     temperature=0.6,
#     top_p=0.95,
#     presence_penalty=0.0,
#     timeout=60,
#     extra_body={
#         "top_k": 20,
#         "min_p": 0.0,
#         "repetition_penalty": 1.0,
#         "chat_template_kwargs": {"enable_thinking": True}
#     },
# )

# Nemotron
# _AGRINET_MODEL_SETTINGS = OpenAIChatModelSettings(
#     temperature=0.6,
#     top_p=0.95,
#     # presence_penalty=0.0,
#     timeout=120,
#     extra_body={
#         # "top_k": 20,
#         # "min_p": 0.0,
#         # "repetition_penalty": 1.0,
#         "chat_template_kwargs": {"enable_thinking": True}
#     },
# )

# Magistarl
_AGRINET_MODEL_SETTINGS = OpenAIChatModelSettings(
    temperature=0.7,
    top_p=0.95,
    timeout=120,
    max_tokens=4096,
)

# GPT-OSS
# _AGRINET_MODEL_SETTINGS = OpenAIChatModelSettings(
#     temperature=1.0,
#     top_p=1.0,
#     timeout=60,
#     openai_reasoning_effort='low',
#     extra_body={
#         "top_k": 100,
#     },
# )

# SYNTHETIC AGRINET AGENT (BASELINE)

agrinet_agent = Agent(
    model=LLM_MODEL,
    name="Vistaar Agent",
    instrument=ENABLE_INSTRUMENTATION,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    model_settings=_AGRINET_MODEL_SETTINGS,
)

@agrinet_agent.system_prompt(dynamic=True)
def get_system_prompt(ctx: RunContext[FarmerContext]):
    """Get the system prompt for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    prompt = get_prompt(f'agrinet_{lang_code}', context={
        'today_date': deps.get_today_date_str(),
        'crop_season': deps.crop_season,
    })
#    prompt += """\n\nFirst draft your thinking process (inner monologue) encapsulated in [THINK] and [/THINK] tags."""
    return prompt

# CANDIDATE AGRINET AGENT (for arena comparison)

agrinet_candidate_agent = Agent(
    model=LLM_CANDIDATE_MODEL,
    name="Vistaar Candidate Agent",
    instrument=ENABLE_INSTRUMENTATION,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    model_settings=_AGRINET_MODEL_SETTINGS,
) if LLM_CANDIDATE_MODEL else None

if agrinet_candidate_agent:
    @agrinet_candidate_agent.system_prompt(dynamic=True)
    def get_candidate_system_prompt(ctx: RunContext[FarmerContext]):
        """Get the system prompt for the candidate agrinet agent."""
        deps = ctx.deps
        lang_code = deps.lang_code if deps.lang_code else 'en'
        return get_prompt(f'agrinet_{lang_code}', context={
            'today_date': deps.get_today_date_str(),
            'crop_season': deps.crop_season,
        })
