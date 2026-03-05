from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt
from synthetic.models import LLM_MODEL, ENABLE_INSTRUMENTATION
from synthetic.tools import TOOLS
from pydantic_ai.settings import ModelSettings
#from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings
from synthetic.deps import FarmerContext

# SYNTHETIC AGRINET AGENT

agrinet_agent = Agent(
    model=LLM_MODEL,
    name="Vistaar Agent",
    instrument=ENABLE_INSTRUMENTATION,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    # model_settings=OpenAIChatModelSettings(
    #     parallel_tool_calls=True,
    # ),
    model_settings=OpenAIChatModelSettings(
        temperature=0.6,
        top_p=0.95,
        presence_penalty=1.5,
        extra_body={
            "top_k": 20,
            "min_p": 0.0,
            "chat_template_kwargs": {"enable_thinking": True}
            },
   )
)

@agrinet_agent.system_prompt(dynamic=True)
def get_system_prompt(ctx: RunContext[FarmerContext]):
    """Get the system prompt for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    return get_prompt(f'agrinet_{lang_code}', context={
        'today_date': deps.get_today_date_str(),
        'crop_season': deps.crop_season,
    })
