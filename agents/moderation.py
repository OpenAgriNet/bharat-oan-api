from pydantic import BaseModel, Field
from typing import Literal
from pydantic_ai import Agent, NativeOutput
from helpers.utils import get_prompt
from dotenv import load_dotenv
from pydantic_ai.models import ModelSettings
from agents.models import LLM_MODEL

# TODO: Add tools from tools/scheme.py
load_dotenv()


class QueryModerationResult(BaseModel):
    """Moderation result of the query."""
    category: Literal["valid_agricultural",
                      "invalid_language",
                      "invalid_non_agricultural",
                      "invalid_external_reference",
                      "invalid_compound_mixed",
                      "unsafe_illegal",
                      "political_controversial",
                      "role_obfuscation"] = Field(..., description="Moderation category of the user's message.")
    action: str = Field(..., description="Action to take on the query, always in English.")

    def __str__(self):
        category_str = self.category.replace("_", " ").title()
        return f"**Moderation Recommendation:** {self.action} ({category_str})"

moderation_agent = Agent(
    model=LLM_MODEL,
    name="Moderation Agent",
    system_prompt=get_prompt('moderation_system'),
    instrument=True,
    output_type=NativeOutput(QueryModerationResult),
    retries=3,
    model_settings=ModelSettings(
        openai_reasoning_effort='low',  
        #max_tokens=350,  # Increased slightly to ensure complete reasoning + category
        temperature=0.5,  # Absolute determinism for consistent outputs
        top_p=0.95,       # Slightly higher to ensure all valid options are considered
        parallel_tool_calls=False,
    )
)
