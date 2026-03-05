"""
Synthetic farmer user agent — an LLM that role-plays as an Indian farmer
to drive multi-turn conversations with the agrinet agent.
"""

from typing import Union

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings

from helpers.utils import get_prompt
from synthetic.models import LLM_MODEL, ENABLE_INSTRUMENTATION
from synthetic.user.profile import FarmerProfile
from synthetic.user.tools import EndConversation, check_otp

user_agent = Agent[FarmerProfile, Union[str, EndConversation]](
    model=LLM_MODEL,
    name="Farmer User",
    instrument=False,
    output_type=Union[str, EndConversation],  # type: ignore[arg-type]
    deps_type=FarmerProfile,
    retries=3,
    end_strategy='exhaustive',
    tools=[Tool(check_otp, takes_ctx=True)],
    # model_settings=OpenAIChatModelSettings(
    #     max_tokens=512,
    #     extra_body={'reasoning_effort': 'high'},
    # ),
    model_settings=OpenAIChatModelSettings(
        temperature=0.7,
        top_p=0.8,
        presence_penalty=1.5,

        extra_body={
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False}
            },
    ),
)


@user_agent.system_prompt
async def system_prompt(ctx: RunContext[FarmerProfile]) -> str:
    profile = ctx.deps
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
        "verbosity": profile.verbosity,
        "language": profile.language,
        "use_latin_script": profile.use_latin_script,
        "scenario_description": profile.scenario["description"],
        "shc_cycle": profile.shc_cycle,
        "grievance_reg_no": profile.grievance_reg_no,
    })
