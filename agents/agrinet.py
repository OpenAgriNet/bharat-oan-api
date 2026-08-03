from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt, get_today_date_str, get_crop_season
from helpers.scheme_catalog.models import CatalogSnapshot
from helpers.scheme_catalog.render import build_prompt_context
from helpers.scheme_catalog.store import pin_catalog_snapshot
from agents.models import AGRINET_MODEL
from agents.tools import TOOLS
from pydantic_ai.models.openai import OpenAIChatModelSettings
from agents.deps import FarmerContext


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
    if deps.scheme_catalog:
        try:
            snapshot = CatalogSnapshot.model_validate(deps.scheme_catalog)
        except Exception:
            snapshot = pin_catalog_snapshot()
    else:
        snapshot = pin_catalog_snapshot()
    context = {
        "today_date": get_today_date_str(),
        "crop_season": get_crop_season(),
        **build_prompt_context(snapshot),
    }
    return get_prompt(f"agrinet_{lang_code}", context=context)