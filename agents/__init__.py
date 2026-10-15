from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

# Off by default: Langfuse @observe builds chain.chat → agent.moderation → agent.vistaar → tool:*.
Agent.instrument_all(False)
