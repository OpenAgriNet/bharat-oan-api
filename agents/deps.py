from typing import Any, Optional
from pydantic import BaseModel, Field
from langcodes import Language


class FarmerContext(BaseModel):
    """Context for the farmer agent.
    
    Args:
        query (str): The user's question.
        lang_code (str): The language code of the user's question.
        session_id (str): The session ID for the conversation.
        moderation_str (Optional[str]): The moderation result of the user's question.
        latitude (Optional[float]): User's latitude for geocoding.
        longitude (Optional[float]): User's longitude for geocoding.


    Example:
        **User:** "What is the weather in Mumbai?"
        **Selected Language:** Hindi
        **Moderation Compliance:** "Valid Agricultural (This is a valid agricultural question.)"
    """
    query: str = Field(description="The user's question.")
    lang_code: str = Field(description="The language code of the user's question.", default='hi')
    session_id: str = Field(description="The session ID for the conversation.")
    question_id: str = Field(
        default="",
        description="Question ID (qid) from the chat API for telemetry correlation.",
    )
    moderation_str: Optional[str] = Field(default=None, description="The moderation result of the user's question.")
    latitude: Optional[float] = Field(default=None, description="User's latitude for geocoding.")
    longitude: Optional[float] = Field(default=None, description="User's longitude for geocoding.")
    npss_used: bool = Field(default=False, description="Whether NPSS image analysis was used in this turn.")
    npss_source_name: Optional[str] = Field(default=None, description="Official NPSS source name.")
    npss_source_owner: Optional[str] = Field(default=None, description="Official NPSS source owner.")
    npss_source_url: Optional[str] = Field(default=None, description="Official NPSS source URL.")
    npss_raw_result: Optional[dict[str, Any]] = Field(default=None, description="Raw NPSS API result for this turn.")
    npss_location_required: bool = Field(
        default=False,
        description="Whether NPSS needs one farmer-provided place before image analysis can continue.",
    )
    npss_missing_location_fields: list[str] = Field(default_factory=list)
    npss_location_needs_confirmation: bool = False
    npss_location_followup: bool = Field(
        default=False,
        description="Whether this turn is the single permitted NPSS location follow-up.",
    )

    def update_moderation_str(self, moderation_str: str):
        """Update the moderation result of the user's question."""
        self.moderation_str = moderation_str

    def mark_npss_result(
        self,
        raw_result: dict[str, Any],
        source_name: str,
        source_owner: str,
        source_url: str,
    ) -> None:
        """Record successful NPSS usage for deterministic response post-processing."""
        self.npss_used = True
        self.npss_raw_result = raw_result
        self.npss_source_name = source_name
        self.npss_source_owner = source_owner
        self.npss_source_url = source_url

    def mark_npss_location_required(
        self,
        missing_fields: list[str],
        *,
        needs_confirmation: bool = False,
    ) -> None:
        """Record that the current image analysis is waiting for location details."""
        self.npss_location_required = True
        self.npss_missing_location_fields = missing_fields
        self.npss_location_needs_confirmation = needs_confirmation

    def _language_string(self):
        """Get the language string for the agrinet agent."""
        if self.lang_code:
            return f"**Selected Language:** {Language.get(self.lang_code).display_name()}"
        else:
            return None
    
    def _query_string(self):
        """Get the query string for the agrinet agent."""
        return "**User:** " + '"' + self.query + '"'

    def _moderation_string(self):
        """Get the moderation string for the agrinet agent."""
        if self.moderation_str:
            return self.moderation_str
        else:
            return None

    def _location_context_string(self):
        """Give the agent explicit access to browser coordinates when available."""
        if self.latitude is not None and self.longitude is not None:
            return (
                "**Browser Location Coordinates:** "
                f"Latitude={self.latitude}, Longitude={self.longitude}"
            )
        return None
    
    def get_user_message(self):
        """Get the user message for the agrinet agent."""
        strings = [
            self._query_string(),
            self._language_string(),
            self._moderation_string(),
            self._location_context_string(),
        ]
        return "\n".join([x for x in strings if x])
