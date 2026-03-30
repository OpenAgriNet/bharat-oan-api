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
    instrument=False,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    model_settings=ModelSettings(
        max_tokens=8192,
        parallel_tool_calls=True,
   )
)

@agrinet_agent.instructions
def get_instructions(ctx: RunContext[FarmerContext]):
    """Get the instructions for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    return get_prompt(f'agrinet_{lang_code}', context={'today_date': get_today_date_str()})