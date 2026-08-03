import os
from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt, get_today_date_str, get_crop_season, get_logger
from helpers.scheme_qdrant_search import format_vector_schemes_prompt_block
from agents.models import AGRINET_MODEL
from agents.tools import TOOLS
from pydantic_ai.models.openai import OpenAIChatModelSettings
from agents.deps import FarmerContext

logger = get_logger(__name__)


agrinet_agent = Agent(
    model=AGRINET_MODEL,
    name="Vistaar Agent",
    instrument=False,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy='exhaustive',
    model_settings=OpenAIChatModelSettings(
        temperature=0.7,
        top_p=0.95,
        max_tokens=4096,
        timeout=120,
        parallel_tool_calls=True,
    )
)

@agrinet_agent.system_prompt(dynamic=True)
def get_system_prompt(ctx: RunContext[FarmerContext]):
    """Get the system prompt for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    scheme_block = format_vector_schemes_prompt_block()
    context = {
        'today_date': get_today_date_str(),
        'crop_season': get_crop_season(),
        **scheme_block,
    }
    # Rendered fresh every turn (unlike the tool docstring, frozen at startup —
    # see agents/tools/search.py) — this confirms live per-turn freshness
    # directly in the logs without needing a Redis GUI or a restart to check.
    logger.info(
        "System prompt rendered for session=%s lang=%s — %s vector schemes live",
        getattr(deps, "session_id", None), lang_code, scheme_block.get("vector_scheme_count"),
    )
    return get_prompt(f'agrinet_{lang_code}', context=context)