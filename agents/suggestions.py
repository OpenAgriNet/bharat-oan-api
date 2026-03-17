from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.settings import ModelSettings
from typing import List
from helpers.utils import get_prompt
from dotenv import load_dotenv
from agents.models import LLM_AGRINET_MODEL
load_dotenv()

suggestions_agent = Agent(
    name="Suggestions Agent",
    model=LLM_AGRINET_MODEL,
    instructions=get_prompt('suggestions_system'),
    instrument=False,
    output_type=PromptedOutput(List[str]),
    retries=3,
    end_strategy='exhaustive',
    tools=[],
    model_settings=ModelSettings(
        parallel_tool_calls=False,  
        openai_reasoning_effort='medium',
        temperature=1.0,
        top_p=1.0,
        top_k=0,
    ) 
)