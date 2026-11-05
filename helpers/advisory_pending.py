"""Guard: while `search_advisory` has a question for the farmer pending, other crop searches wait.

`search_advisory` returns a result that tells the agent to ask the farmer something (the season, the
sowing days, or an offer of other seasons) and to give no advice yet. Those results carry
`PENDING_MARKER`. Without a guard a model can skip that question by calling `search_documents` or
`search_pests_diseases` in the same turn and answering from them. The check is on the message history,
so it works the same for every model.
"""
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

# Kept as an HTML comment: it is not meant to be shown to the farmer.
PENDING_MARKER = "<!-- advisory-question-pending -->"

PENDING_RETRY = (
    "Do not search yet. The crop advisory search asked you to put a question to the farmer first. "
    "Ask that question now, give no advice, and wait for the farmer's answer."
)


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
