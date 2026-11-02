"""Shared check for the crop advisory "ask first" rule.

Every crop advisory question must be followed by a "generic or personalised?" question and the
farmer's reply before any advisory search runs. The check works on the message history the tool
sees (`ctx.messages`), so it holds even when the model tries another search tool first.
"""
import re

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart

# Appears in the search_advisory result when the farmer's district has no advisory data. A result
# carrying it is not an answer: the agent is expected to ask the farmer about it next.
NO_DISTRICT_DATA_MARKER = "[NO_ADVISORY_DATA_FOR_DISTRICT]"

# Tools whose result answers a crop question (so a message written after one is not an ask).
ANSWER_TOOLS = {"search_advisory", "search_pests_diseases", "search_documents"}

ASK_FIRST_RETRY = (
    "Do not search yet. Ask the farmer whether they want generic or personalised advice, "
    "wait for their answer, then search. Ask it once, and do not repeat a question you have already written."
)


def _is_answer(part) -> bool:
    return (
        isinstance(part, ToolReturnPart)
        and part.tool_name in ANSWER_TOOLS
        and NO_DISTRICT_DATA_MARKER not in str(part.content)
    )


def _has_text(message) -> bool:
    return isinstance(message, ModelResponse) and any(
        isinstance(part, TextPart) and part.content.strip() for part in message.parts
    )


def farmer_was_asked(ctx) -> bool:
    """True when the farmer's latest message answers a "generic or personalised?" question.

    The assistant message just before the farmer's latest message must be an ask: it must not
    have been written after an answer (a search result) in the same turn. So a new question after
    an answer, or an answer followed by a late "generic or personalised?", needs its own ask.
    """
    messages = getattr(ctx, "messages", None)
    if messages is None:  # cannot verify (not running inside an agent run)
        return True
    messages = list(messages)

    latest = None  # the farmer's latest message
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in messages[index].parts
        ):
            latest = index
            break
    if latest is None:
        return False

    ask = None  # the assistant message right before it
    for index in range(latest - 1, -1, -1):
        if _has_text(messages[index]):
            ask = index
            break
    if ask is None:
        return False

    # Walk back to the farmer message that started the turn containing the ask.
    for index in range(ask - 1, -1, -1):
        message = messages[index]
        if isinstance(message, ModelRequest):
            if any(isinstance(part, UserPromptPart) for part in message.parts):
                break
            if any(_is_answer(part) for part in message.parts):
                return False  # the ask came after an answer, so it is not an ask for this question
    return True


_CROP_WORDS = (
    "wheat rice paddy sorghum jowar maize corn bajra millet millets ragi kodo barley oats cotton sugarcane "
    "groundnut peanut soybean soyabean mustard rapeseed sunflower chickpea gram lentil moong urad tur arhar "
    "pigeonpea potato tomato onion brinjal chilli chili cabbage cauliflower okra bhindi pea peas banana mango "
    "grape grapes tea coffee jute tobacco ajwain turmeric ginger garlic sesame castor safflower linseed "
    "coconut apple orange pomegranate guava papaya crop crops vegetable vegetables"
).split()
_CROP_RE = re.compile(r"\b(?:" + "|".join(_CROP_WORDS) + r")\b", re.IGNORECASE)


def mentions_crop(query: str) -> bool:
    """True when an English search query names a crop (used for the general document search)."""
    return bool(_CROP_RE.search(str(query or "")))
