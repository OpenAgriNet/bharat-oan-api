from copy import deepcopy
from dataclasses import replace
from typing import List, Optional

from pydantic import BaseModel, Field
from langcodes import Language
from datetime import datetime
from pydantic_ai.messages import ModelResponse, ThinkingPart
from helpers.utils import get_crop_season


class FarmerContext(BaseModel):
    """Context for the farmer agent.

    Args:
        query (str): The user's question.
        lang_code (str): The language code of the user's question.
        session_id (str): The session ID for the conversation.
        moderation_str (Optional[str]): The moderation result of the user's question.
    """
    query: str = Field(description="The user's question.")
    lang_code: str = Field(description="The language code of the user's question.", default='hi')
    session_id: str = Field(description="The session ID for the conversation.")
    moderation_str: Optional[str] = Field(default=None, description="The moderation result of the user's question.")
    today_date: datetime = Field(description="The today's date.")

    # Farmer profile fields (populated during synthetic generation so mock
    # tools can return data consistent with the simulated farmer identity).
    farmer_name: Optional[str] = None
    farmer_phone: Optional[str] = None
    farmer_aadhaar: Optional[str] = None
    farmer_state: Optional[str] = None
    farmer_district: Optional[str] = None
    farmer_village: Optional[str] = None
    farmer_crops: Optional[list[str]] = None
    farmer_land_acres: Optional[float] = None

    def update_moderation_str(self, moderation_str: str):
        """Update the moderation result of the user's question."""
        self.moderation_str = moderation_str

    def _language_string(self):
        if self.lang_code:
            return f"**Selected Language:** {Language.get(self.lang_code).display_name()}"
        return None

    def _query_string(self):
        return "**User:** " + '"' + self.query + '"'

    def _moderation_string(self):
        if self.moderation_str:
            return self.moderation_str
        return None

    def get_user_message(self):
        strings = [self._query_string(), self._language_string(), self._moderation_string()]
        return "\n".join([x for x in strings if x])

    def get_today_date_str(self) -> str:
        """Format today_date as 'Monday, 23 May 2025'."""
        return self.today_date.strftime('%A, %d %B %Y')

    @property
    def crop_season(self) -> str:
        """Current Indian agricultural season based on today_date."""
        return get_crop_season(self.today_date)


# ---------------------------------------------------------------------------
# Conversation history helpers for moderation context
# ---------------------------------------------------------------------------


def get_message_pairs(history: list, limit: int = None) -> List[List]:
    """Extract user/assistant message part pairs from history (newest first).

    Args:
        history: List of ModelMessage objects (pydantic-ai message history).
        limit: Maximum number of pairs to return (None = all).

    Returns:
        List of [UserPromptPart, TextPart] pairs, newest first.
    """
    if not history:
        return []

    pairs = []
    i = len(history) - 1

    while i > 0 and (limit is None or len(pairs) < limit):
        # Find nearest assistant text part
        assistant_idx = None
        text_part = None
        for j in range(i, -1, -1):
            for part in history[j].parts:
                if getattr(part, "part_kind", "") == "text":
                    assistant_idx = j
                    text_part = part
                    break
            if assistant_idx is not None:
                break

        if assistant_idx is None or text_part is None:
            break

        # Find nearest user prompt part before the assistant message
        user_idx = None
        user_part = None
        for j in range(assistant_idx - 1, -1, -1):
            for part in history[j].parts:
                if getattr(part, "part_kind", "") == "user-prompt":
                    user_idx = j
                    user_part = part
                    break
            if user_idx is not None:
                break

        if user_idx is None or user_part is None:
            break

        pairs.append([deepcopy(user_part), deepcopy(text_part)])
        i = user_idx - 1

    return pairs


def format_message_pairs(history: list, limit: int = None) -> List[str]:
    """Format user/assistant message pairs as strings.

    Args:
        history: List of ModelMessage objects (pydantic-ai message history).
        limit: Maximum number of pairs to return (None = all).

    Returns:
        List of formatted strings with user and assistant messages.
    """
    pairs = get_message_pairs(history, limit)
    formatted = []
    for user_part, assistant_part in pairs:
        formatted.append(
            f"**User Message**:\n{user_part.content}\n\n"
            f"**Assistant Message**:\n{assistant_part.content}"
        )
    return formatted


def strip_thinking(history: list) -> list:
    """Remove ThinkingPart from ModelResponse messages so thinking traces
    are not sent back to the model on subsequent turns.

    Returns a new list; the original is not mutated.
    """
    cleaned = []
    for msg in history:
        if isinstance(msg, ModelResponse):
            filtered = [p for p in msg.parts if not isinstance(p, ThinkingPart)]
            cleaned.append(replace(msg, parts=filtered) if filtered != list(msg.parts) else msg)
        else:
            cleaned.append(msg)
    return cleaned


def build_moderation_input(user_text: str, agrinet_history: list, limit: int = 3) -> str:
    """Build the moderation prompt with conversation context.

    Prepends the last ``limit`` QA pairs from *agrinet_history* (if any)
    before the current user message so the moderation agent can evaluate
    the message in context.

    Args:
        user_text: The current user message to moderate.
        agrinet_history: The agrinet agent's message history.
        limit: Number of recent QA pairs to include (default 3).

    Returns:
        A string ready to pass as the user_prompt to moderation_agent.run().
    """
    message_pairs = "\n\n".join(format_message_pairs(agrinet_history, limit))
    if message_pairs:
        return f"**Conversation**\n\n{message_pairs}\n\n---\n\n{user_text}"
    return user_text
