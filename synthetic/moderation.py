from pydantic import BaseModel, Field
from typing import Literal
from pydantic_ai import Agent, PromptedOutput
from helpers.utils import get_prompt
from dotenv import load_dotenv
from pydantic_ai.models import ModelSettings
from synthetic.models import LLM_MODERATION_MODEL, ENABLE_INSTRUMENTATION

# TODO: Add tools from tools/scheme.py
load_dotenv()


class QueryModerationResult(BaseModel):
    """Moderation result of the query."""
    category: Literal["valid_agricultural",
                      "invalid_non_agricultural",
                      "invalid_external_reference",
                      "invalid_compound_mixed",
                      "invalid_language",
                      "unsafe_illegal",
                      "political_controversial",
                      "role_obfuscation"] = Field(..., description="Moderation category of the user's message.")
    action: str = Field(..., description="Action to take on the query, always in English.")

    def __str__(self):
        category_str = self.category.replace("_", " ").title()
        return f"**Moderation Compliance:** {self.action} ({category_str})"

moderation_agent = Agent(
    model=LLM_MODERATION_MODEL,
    name="Moderation Agent",
    instructions=get_prompt('moderation_system'),
    instrument=False,
    output_type=PromptedOutput(QueryModerationResult),
    retries=3,
    model_settings=ModelSettings(
        temperature=0.0,
        top_p=1.0,
        openai_reasoning_effort='low',
    )    
)
