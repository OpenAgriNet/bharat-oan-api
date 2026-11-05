"""Guard: while `search_advisory` has a question for the farmer pending, other crop searches wait.

`search_advisory` returns a result that tells the agent to ask the farmer something (the season, the
sowing days, or an offer of other seasons) and to give no advice yet. Those results carry
`PENDING_MARKER`. Without a guard a model can skip that question by calling `search_documents` or
`search_pests_diseases` in the same turn and answering from them. The check is on the message history,
so it works the same for every model.
"""
import re

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

# Kept as an HTML comment: it is not meant to be shown to the farmer.
PENDING_MARKER = "<!-- advisory-question-pending -->"

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

def _has_history(ctx) -> bool:
    return bool(getattr(ctx, "messages", None))


def _farmer_text(ctx) -> str:
    parts = []
    for message in getattr(ctx, "messages", None) or []:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    parts.append(str(part.content))
    return "\n".join(parts)


def question_asked_earlier(ctx) -> bool:
    """True when `search_advisory` asked the farmer something in an earlier turn (not this one)."""
    messages = list(getattr(ctx, "messages", None) or [])
    turn = _current_turn(ctx)
    earlier = messages[: len(messages) - len(turn)]
    for message in earlier:
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


def farmer_gave_season(ctx) -> bool:
    """True when the farmer can have given a season: they were asked earlier, or a season word is in their text."""
    if not _has_history(ctx):
        return True
    return question_asked_earlier(ctx) or bool(_SEASON_WORDS.search(_farmer_text(ctx)))


def farmer_gave_days(ctx) -> bool:
    """True when the farmer can have given the sowing days: asked earlier, or a number or a count word is in their text."""
    if not _has_history(ctx):
        return True
    text = _farmer_text(ctx)
    return (
        question_asked_earlier(ctx)
        or any(ch.isdigit() for ch in text)
        or bool(_NUMBER_WORDS.search(text))
    )


def may_answer_now(ctx) -> bool:
    """`answer_now` is honoured only after the farmer was asked in an earlier turn, never in the same turn."""
    if not _has_history(ctx):
        return True
    return question_asked_earlier(ctx)
