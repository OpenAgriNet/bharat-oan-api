from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from typing import List
from helpers.utils import get_prompt
from dotenv import load_dotenv
from agents.models import LLM_MODEL
load_dotenv()

suggestions_agent = Agent(
    name="Suggestions Agent",
    model=LLM_MODEL,
    system_prompt=get_prompt('suggestions_system'),
    instrument=True,
    output_type=List[str],
    retries=3,
    end_strategy='exhaustive',
    tools=[],
    model_settings=ModelSettings(
        parallel_tool_calls=True,
        openai_reasoning_effort='medium',
        temperature=1.0,
        top_p=1.0,
        top_k=0,
    ) # Prevent multiple tool calls
)