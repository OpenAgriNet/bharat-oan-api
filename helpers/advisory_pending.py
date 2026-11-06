"""Guard: while `search_advisory` has a question for the farmer pending, other crop searches wait.

`search_advisory` returns a result that tells the agent to ask the farmer something (the season, the
sowing days, or an offer of other seasons) and to give no advice yet. Those results carry
`PENDING_MARKER`. Without a guard a model can skip that question by calling `search_documents` or
`search_pests_diseases` in the same turn and answering from them. The check is on the message history,
so it works the same for every model.
"""
import re

from helpers.locations import resolve_place
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

# Kept as an HTML comment: it is not meant to be shown to the farmer.
PENDING_MARKER = "<!-- advisory-question-pending -->"
# Added to the pending question that asks for the farmer's place (state / district).
LOCATION_MARKER = "<!-- advisory-location-question -->"

PENDING_RETRY = (
    "Do not search yet. The crop advisory search asked you to put a question to the farmer first. "
    "Ask that question now, give no advice, and wait for the farmer's answer."
)

ADVISORY_FIRST_RETRY = (
    "Call `search_advisory` first for this crop question, with the same crop, then continue. "
    "Do not answer from this tool alone."
)


def _current_turn(ctx) -> list:
    """Messages since the farmer's latest message."""
    messages = getattr(ctx, "messages", None)
    if not messages:
        return []
    messages = list(messages)
    turn_start = 0
    for index, message in enumerate(messages):
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            turn_start = index
    return messages[turn_start:]


def advisory_not_searched(ctx) -> bool:
    """True when `search_advisory` has not been called yet in the current turn."""
    for message in _current_turn(ctx):
        if isinstance(message, ModelResponse) and any(
            isinstance(p, ToolCallPart) and p.tool_name == "search_advisory" for p in message.parts
        ):
            return False
    return True


def advisory_question_pending(ctx) -> bool:
    """True when `search_advisory` returned a question for the farmer earlier in the current turn."""
    messages = getattr(ctx, "messages", None)
    if not messages:
        return False
    messages = list(messages)
    turn_start = 0
    for index, message in enumerate(messages):
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            turn_start = index
    for message in messages[turn_start:]:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == "search_advisory"
                and PENDING_MARKER in str(part.content)
            ):
                return True
    return False


# --- Hard guards: the model may not fill in what only the farmer can say -----------------------------------
# A weaker model sometimes passes `season`, `days_after_sowing` or `answer_now` that the farmer never gave,
# so the tool never asks. These checks read the conversation instead of trusting the arguments. They work
# the same for every model. They do nothing when there is no history to read (direct calls, tests).

# Season names as a farmer may write them, in English and in the supported scripts.
_SEASON_WORDS = re.compile(
    r"rabi|rabbi|kharif|khareef|kharip|zaid|zayad|jayad|"
    r"रबी|रब्बी|खरीफ|खरीप|जायद|জায়েদ|রবি|খরিফ|রব্বী|খৰিফ|ৰবি|"
    r"રવિ|ખરીફ|ಹಿಂಗಾರು|ಮುಂಗಾರು|ರಬಿ|ಖಾರಿಫ್|ಖರೀಫ್|റാബി|ഖരീഫ്|ରବି|ଖରିଫ|"
    r"ਹਾੜ੍ਹੀ|ਹਾੜੀ|ਸਾਉਣੀ|ரபி|காரிஃப்|கரீஃப்|రబీ|ఖరీఫ్|ज़ायद",
    re.IGNORECASE,
)
_NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|hundred|"
    r"week|weeks|month|months|yesterday)\b",
    re.IGNORECASE,
)

# The agent's prompt is "**Conversation**\n\n<last message pairs>\n\n---\n\n<the farmer's message>", so the
# farmer's own words are only what follows the last separator. Reading the whole prompt would pick up
# season words and numbers from earlier answers.
_PROMPT_SEPARATOR = "\n\n---\n\n"


def _has_history(ctx) -> bool:
    return bool(getattr(ctx, "messages", None))


def _farmer_message(ctx) -> str:
    """The farmer's message for this turn, without the conversation text added in front of it."""
    for message in _current_turn(ctx):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content).rsplit(_PROMPT_SEPARATOR, 1)[-1]
    return ""


def _is_pending_return(part) -> bool:
    return (
        isinstance(part, ToolReturnPart)
        and part.tool_name == "search_advisory"
        and PENDING_MARKER in str(part.content)
    )


def _earlier_turns(ctx) -> list:
    messages = list(getattr(ctx, "messages", None) or [])
    return messages[: len(messages) - len(_current_turn(ctx))]


def asked_in_previous_turn(ctx) -> bool:
    """True when the turn right before this one ended with a `search_advisory` question for the farmer.

    Only then is this message the farmer's answer to that question. A question asked further back does
    not count: a new question later in the chat has to be asked about again."""
    earlier = _earlier_turns(ctx)
    start = 0
    for index, message in enumerate(earlier):
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            start = index
    return any(
        isinstance(message, ModelRequest) and any(_is_pending_return(p) for p in message.parts)
        for message in earlier[start:]
    )


def _tool_history_missing(ctx) -> bool:
    """True when earlier turns exist but none of their tool results were kept, so nothing can be checked."""
    earlier = _earlier_turns(ctx)
    has_earlier_turn = any(
        isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts) for m in earlier
    )
    has_tool_part = any(
        isinstance(m, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in m.parts) for m in earlier
    )
    return has_earlier_turn and not has_tool_part


def asked_location_in_previous_turn(ctx) -> bool:
    """True when the turn right before this one asked the farmer for their place (state / district)."""
    earlier = _earlier_turns(ctx)
    start = 0
    for index, message in enumerate(earlier):
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            start = index
    return any(
        isinstance(message, ModelRequest)
        and any(_is_pending_return(p) and LOCATION_MARKER in str(p.content) for p in message.parts)
        for message in earlier[start:]
    )


def farmer_gave_place(ctx) -> bool:
    """True when this message can carry the farmer's place: it answers our place question, or names a place.

    Two-letter abbreviations are ignored when scanning ordinary prose ("up" is a word)."""
    if not _has_history(ctx):
        return True
    return asked_location_in_previous_turn(ctx) or resolve_place(_farmer_message(ctx), allow_short=False).known


def farmer_wrote(ctx, text: str) -> bool:
    """True when `text` (a place the model passed) is in the farmer's message, or answers our place question."""
    if not _has_history(ctx):
        return True
    wanted = " ".join(str(text or "").lower().split())
    if not wanted:
        return False
    return asked_location_in_previous_turn(ctx) or wanted in " ".join(_farmer_message(ctx).lower().split())


def farmer_gave_season(ctx) -> bool:
    """True when this message can carry a season: it answers our question, or it names a season itself."""
    if not _has_history(ctx):
        return True
    return asked_in_previous_turn(ctx) or bool(_SEASON_WORDS.search(_farmer_message(ctx)))


def farmer_gave_days(ctx) -> bool:
    """True when this message can carry the sowing days: it answers our question, or has a number or count word."""
    if not _has_history(ctx):
        return True
    text = _farmer_message(ctx)
    return (
        asked_in_previous_turn(ctx)
        or any(ch.isdigit() for ch in text)
        or bool(_NUMBER_WORDS.search(text))
    )


def may_answer_now(ctx) -> bool:
    """`answer_now` is honoured only when this message answers our question (never in the turn it was asked)."""
    if not _has_history(ctx):
        return True
    return asked_in_previous_turn(ctx) or _tool_history_missing(ctx)
