import os
from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt, get_today_date_str
from agents.models import LLM_MODEL
from agents.tools import TOOLS
from pydantic_ai.settings import ModelSettings
from agents.deps import FarmerContext


agrinet_agent = Agent(
    model=LLM_MODEL,
    name="Vistaar Agent",
    instrument=True,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    model_settings=ModelSettings(
        temperature=1.0,
        max_tokens=10240,
        top_p=1.0,
        top_k=0,
        parallel_tool_calls=True,  
        openai_reasoning_effort='medium',  
        openai_send_reasoning_ids=False,
   )
)

@agrinet_agent.system_prompt(dynamic=True)
def get_system_prompt(ctx: RunContext[FarmerContext]):
    """Get the system prompt for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    return get_prompt(f'agrinet_{lang_code}', context={'today_date': get_today_date_str()})