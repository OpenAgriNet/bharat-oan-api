import os
from pydantic_ai import Agent
from dotenv import load_dotenv

load_dotenv()

# Off by default: Langfuse @observe builds production → agent.moderation → agent.vistaar → tool:*.
Agent.instrument_all(False)
