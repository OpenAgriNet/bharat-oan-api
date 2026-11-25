from pydantic_ai import RunContext
from helpers.utils import get_today_date_str, get_crop_season, get_last_weekday_table, get_logger
from agents.models import configured_agent
from agents.tools import TOOLS
from agents.tools.khc import khc_service_note
from pydantic_ai.models.openai import OpenAIChatModelSettings
from agents.deps import FarmerContext
from agents.skill_catalog import get_catalog
from agents.skills import SKILL_LOADER, prepare_skill_tools, render_prompt


logger = get_logger(__name__)
get_catalog().validate_tools(TOOLS)


agrinet_agent = configured_agent(
    "agrinet",
    name="Vistaar Agent",
    instrument=False,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=[*TOOLS, SKILL_LOADER],
    prepare_tools=prepare_skill_tools,
    end_strategy='exhaustive',
    model_settings=OpenAIChatModelSettings(
        temperature=0.7,
        top_p=0.95,
        max_tokens=4096,
        parallel_tool_calls=True,
    )
)

@agrinet_agent.system_prompt(dynamic=True)
def get_system_prompt(ctx: RunContext[FarmerContext]):
    """Get the system prompt for the agrinet agent."""
    deps = ctx.deps
    lang_code = deps.lang_code if deps.lang_code else 'en'
    context = {
        'today_date': get_today_date_str(),
        'crop_season': get_crop_season(),
        'last_weekday_table': get_last_weekday_table(),
    }
    deps.skills.restore(ctx.messages)
    context['khc_service_note'] = khc_service_note(ctx.messages, current_query=deps.query)
    return render_prompt(deps.skills, lang_code, context)
