"""
Synthetic farmer user agent — an LLM that role-plays as an Indian farmer
to drive multi-turn conversations with the agrinet agent.
"""

from typing import Union

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings

from helpers.utils import get_prompt
from synthetic.models import LLM_MODEL
from synthetic.user.profile import FarmerProfile
from synthetic.user.tools import EndConversation, check_otp

user_agent = Agent[FarmerProfile, Union[str, EndConversation]](
    model=LLM_MODEL,
    name="Farmer User",
    instrument=True,
    output_type=Union[str, EndConversation],  # type: ignore[arg-type]
    deps_type=FarmerProfile,
    retries=3,
    end_strategy='exhaustive',
    tools=[Tool(check_otp, takes_ctx=True)],
    model_settings=OpenAIChatModelSettings(
        max_tokens=512,
        extra_body={'reasoning_effort': 'high'},
    ),
    # model_settings=AnthropicModelSettings(
    #     temperature=1.0,
    #     max_tokens=512,
    #     thinking={'type':'adaptive'},
    #     anthropic_effort='low',
    # ),
)


@user_agent.system_prompt
async def system_prompt(ctx: RunContext[FarmerProfile]) -> str:
    profile = ctx.deps
    desc_key = "description_hi" if profile.language == "hi" else "description_en"
    return get_prompt("user_agent", context={
        "name": profile.name,
        "village": profile.village,
        "district": profile.district,
        "state": profile.state,
        "phone": profile.phone,
        "aadhaar": profile.aadhaar,
        "pm_kisan_reg_no": profile.pm_kisan_reg_no,
        "crops": ", ".join(profile.crops),
        "land_acres": profile.land_acres,
        "mood": profile.mood,
        "language": profile.language,
        "scenario_description": profile.scenario[desc_key],
        "shc_cycle": profile.shc_cycle,
        "grievance_reg_no": profile.grievance_reg_no,
    })
