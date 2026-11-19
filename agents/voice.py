"""Voice agent for Sarvam phone calls.

Same tools and same feature instructions as chat: the prompt is the voice base prompt
(assets/prompts/voice/voice_{lang}.md: how to speak, call rules) plus the shared
skills/ workflow files, rendered with channel="voice". The manifest's `voice`
profile decides which features a call has.
"""
import re

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.settings import ModelSettings

from agents.deps import FarmerContext
from agents.model_registry import get_registry
from agents.skills import SKILL_LOADER, prepare_skill_tools, render_prompt
from agents.tools import TOOLS
from agents.tools.feedback import FEEDBACK_TOOL
from agents.tools.khc import khc_service_note
from helpers.langfuse_helper import voice_tracer_provider
from helpers.scheme_qdrant_search import format_vector_schemes_prompt_block
from helpers.utils import get_crop_season, get_last_weekday_table, get_logger, get_today_date_str

logger = get_logger(__name__)

VOICE_USE_CASE = "voice"
VOICE_AGENT = "voice"
# Call-control tools that belong to the voice agent rather than to a shared skill.
VOICE_ONLY_TOOLS = {FEEDBACK_TOOL.name}

# Gemma sometimes narrates a tool call ("I will check the details. One moment
# please.") and ends the turn without calling the tool, so the caller hears a
# promise that never resolves. Only short replies are checked: a real answer
# can legitimately contain words like "wait" ("wait two days after spraying").
_HOLD_MESSAGE_RE = re.compile(
    r"\b(one|a) moment\b|please wait|hold on|bear with me"
    r"|(i will|let me) (check|find|fetch|get|look|retrieve|provide|pull|share)"
    r"|एक (क्षण|पल)|कृपया प्रतीक्षा|प्रतीक्षा करें|रुकिए|इंतज़ार करें|इंतजार करें"
    r"|मैं .{0,20}(देखती|खोजती|निकालती|लाती) हूं",
    re.IGNORECASE,
)
_HOLD_MESSAGE_MAX_LEN = 200


async def prepare_voice_tools(ctx: RunContext[FarmerContext], tools: list) -> list:
    """Same skill visibility as chat, plus the voice-only call tools."""
    shared = await prepare_skill_tools(ctx, [tool for tool in tools if tool.name not in VOICE_ONLY_TOOLS])
    return [*shared, *(tool for tool in tools if tool.name in VOICE_ONLY_TOOLS)]


voice_agent = Agent(
    model=get_registry().get_model(get_registry().get_default_alias(VOICE_USE_CASE)),
    name="Voice Agent",
    output_type=str,
    # Tracing is attached here rather than through the global PydanticAI default, so
    # chat's agents keep their own instrumentation settings. With a separate voice
    # Langfuse project, spans go to its own tracer provider (never the chat project).
    instrument=InstrumentationSettings(include_content=True, version=3, tracer_provider=voice_tracer_provider),
    deps_type=FarmerContext,
    retries=3,
    output_retries=3,
    tools=[*TOOLS, SKILL_LOADER, FEEDBACK_TOOL],
    prepare_tools=prepare_voice_tools,
    end_strategy='exhaustive',
    model_settings=ModelSettings(
        parallel_tool_calls=True,
        timeout=30,
    ),
)


@voice_agent.output_validator
def reject_hold_messages(ctx: RunContext[FarmerContext], output: str) -> str:
    audio = (output or "").strip()
    if len(audio) <= _HOLD_MESSAGE_MAX_LEN and _HOLD_MESSAGE_RE.search(audio):
        logger.warning(f"Rejected hold-message reply, retrying: {audio!r}")
        raise ModelRetry(
            "Your reply was only a promise to check and made the caller wait for an "
            "answer that never comes. Do not say you will check or ask the user to "
            "wait. Call the required tool now and answer with the actual information "
            "in this same response. Keep the conversation context: if a scheme or "
            "topic was already being discussed, answer for that same scheme or topic "
            "— do not ask the user to repeat it."
        )
    return output


@voice_agent.instructions
def get_voice_system_prompt(ctx: RunContext[FarmerContext]) -> str:
    """Voice base prompt + the shared feature instructions (same builder as chat)."""
    deps = ctx.deps
    context = {
        'today_date': get_today_date_str(),
        'crop_season': get_crop_season(),
        'last_weekday_table': get_last_weekday_table(),
        **format_vector_schemes_prompt_block(),
    }
    deps.skills.restore(ctx.messages)
    context['khc_service_note'] = khc_service_note(ctx.messages, current_query=deps.query, channel="voice")
    return render_prompt(deps.skills, deps.lang_code, context)
