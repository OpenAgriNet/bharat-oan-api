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


def farmer_message(ctx) -> str:
    """The farmer's own words for this turn (public name of `_farmer_message`)."""
    return _farmer_message(ctx)


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


def _split_turns(ctx) -> list:
    """The conversation as a list of turns; a turn is the messages from one farmer message up to the next."""
    messages = list(getattr(ctx, "messages", None) or [])
    starts = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts)
    ]
    if not starts:
        return []
    return [messages[a:b] for a, b in zip(starts, starts[1:] + [len(messages)])]


def _turn_text(turn: list) -> str:
    for message in turn:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content).rsplit(_PROMPT_SEPARATOR, 1)[-1]
    return ""


def _turn_asked(turn: list, marker: str = "") -> bool:
    """True when `search_advisory` ended this turn with a question for the farmer (with `marker` when given)."""
    return any(
        isinstance(message, ModelRequest) and any(_is_pending_return(p) and marker in str(p.content) for p in message.parts)
        for message in turn
    )


def answer_chain(ctx) -> list:
    """The farmer's messages that belong to the question being worked on, oldest first.

    A question can take several turns (the season and days, then the place...). The chain is this turn plus the
    turns before it, as long as each turn before ended with a question from `search_advisory`. Each item is
    (the farmer's own words, whether it answers a place question). What the farmer said anywhere in the chain
    still counts, so an answer given earlier is never asked for again."""
    turns = _split_turns(ctx)
    if not turns:
        return []
    index = len(turns) - 1
    chain = [index]
    while index > 0 and _turn_asked(turns[index - 1]):
        index -= 1
        chain.insert(0, index)
    return [
        (_turn_text(turns[i]), i > 0 and _turn_asked(turns[i - 1], LOCATION_MARKER))
        for i in chain
    ]


def farmer_gave_place(ctx) -> bool:
    """True when the farmer named a place in this question's chain of messages, or answered our place question.

    Two-letter abbreviations are ignored when scanning ordinary prose ("up" is a word)."""
    if not _has_history(ctx):
        return True
    return any(
        answers_place or resolve_place(text, allow_short=False).known for text, answers_place in answer_chain(ctx)
    )


def farmer_wrote(ctx, text: str) -> bool:
    """True when `text` (a place the model passed) is literally in the farmer's messages of this question."""
    if not _has_history(ctx):
        return True
    wanted = " ".join(str(text or "").lower().split())
    if not wanted:
        return False
    return any(wanted in " ".join(message.lower().split()) for message, _ in answer_chain(ctx))


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


# --- Crop check ---------------------------------------------------------------------------------
# A model that sees earlier turns of the chat sometimes reuses their crop for a new question. The crop has to be
# in the farmer's own words for this question (the current message, plus the answers that belong to it).

# Coffee in English and in the supported scripts; arabica and robusta are coffee varieties, so they name the crop too.
_COFFEE_WORDS = re.compile(
    r"coffee|coffe\b|kaffee|arabica|robusta|कॉफी|कॉफ़ी|कोफी|কফি|કોફી|ಕಾಫಿ|കാപ്പി|କଫି|ਕੌਫੀ|காபி|కాఫీ",
    re.IGNORECASE,
)


# Other names a farmer uses for the same crop; the model often passes the standard English name (paddy -> Rice).
_CROP_SYNONYMS = [
    {"rice", "paddy", "dhan", "dhaan", "धान", "ધાન", "நெல்", "వరి", "ধান", "ଧାନ", "ಭತ್ತ", "നെല്ല്"},
    {"wheat", "gehun", "gehu", "गेहूं", "गेहू", "ઘઉં", "গম", "ଗହମ", "ಗೋಧಿ", "கோதுமை", "గోధుమ", "ਕਣਕ"},
    {"sorghum", "jowar", "ज्वार", "jwari", "jawar"},
    {"maize", "corn", "makka", "makai", "मक्का", "मकई"},
    {"kodo millet", "kodo"},
    {"pearl millet", "bajra", "बाजरा"},
    {"chickpea", "gram", "chana", "चना"},
    {"pigeon pea", "pigeonpea", "tur", "toor", "arhar", "अरहर", "तूर"},
    {"sugarcane", "ganna", "गन्ना"},
    {"cotton", "kapas", "कपास"},
    {"soybean", "soyabean", "soya", "सोयाबीन"},
    {"groundnut", "peanut", "mungfali", "मूंगफली"},
]


def _in_text(word: str, text: str) -> bool:
    """`word` in `text`: whole word for Latin words (so "tur" is not found in "nature"), plain match otherwise."""
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None
    return word in text


def _chain_text(ctx) -> str:
    return " ".join(" ".join(text.lower().split()) for text, _ in answer_chain(ctx))


def farmer_named_crop(ctx, crop: str, crop_words: str = "") -> bool:
    """True when the crop is in the farmer's own words for this question.

    `crop` is the English name the model passed; `crop_words` are the farmer's exact words for it (any script).
    Either one found in the farmer's messages is enough. Coffee also counts when only arabica or robusta is named."""
    if not _has_history(ctx):
        return True
    text = _chain_text(ctx)
    if _COFFEE_WORDS.search(str(crop or "")) and _COFFEE_WORDS.search(text):
        return True
    for candidate in (crop, crop_words):
        wanted = " ".join(str(candidate or "").lower().split())
        if wanted and wanted in text:
            return True
        for group in _CROP_SYNONYMS:
            if wanted in group and any(_in_text(name, text) for name in group):
                return True
    return False
