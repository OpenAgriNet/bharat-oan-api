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


def has_text(message) -> bool:
    return isinstance(message, ModelResponse) and any(
        isinstance(part, TextPart) and part.content.strip() for part in message.parts
    )


def _is_user_turn_start(message) -> bool:
    return isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) for part in message.parts)


def asked_rounds(ctx) -> int:
    """How many questions the agent asked, and the farmer answered, for the current question.

    Messages are split into turns, one per farmer message. Looking back from the farmer's latest
    message, each earlier turn that holds an assistant message and no search result counts as one
    asked-and-answered question. A turn with a search result is an answer, and ends the count, so a
    new question after an answer starts again from zero. A message written after a search result in
    the same turn is not an ask.
    """
    messages = getattr(ctx, "messages", None)
    if messages is None:  # cannot verify (not running inside an agent run)
        return 99
    messages = list(messages)
    starts = [index for index, message in enumerate(messages) if _is_user_turn_start(message)]
    if not starts:
        return 0

    rounds = 0
    for turn_number in range(len(starts) - 2, -1, -1):  # every turn before the latest one
        turn = messages[starts[turn_number]:starts[turn_number + 1]]
        if any(_is_answer(part) for message in turn if isinstance(message, ModelRequest) for part in message.parts):
            break
        if not any(has_text(message) for message in turn):
            break
        rounds += 1
    return rounds


def farmer_was_asked(ctx, rounds: int = 1) -> bool:
    """True when the agent asked the farmer `rounds` questions for the current question and got answers."""
    return asked_rounds(ctx) >= rounds


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

