from typing import Optional
from pydantic import BaseModel, Field
from langcodes import Language
from datetime import datetime
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
