import os
from typing import AsyncGenerator
from fastapi import BackgroundTasks
from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.utils import get_logger
from helpers import langfuse_helper
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
from agents.deps import FarmerContext
from langfuse.decorators import observe, langfuse_context
from langfuse import Langfuse

logger = get_logger(__name__)
langfuse = Langfuse()

MODEL_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")


async def stream_chat_messages(
    query: str,
    session_id: str,
    source_lang: str,
    target_lang: str,
    user_id: str,
    history: list,
    background_tasks: BackgroundTasks
) -> AsyncGenerator[str, None]:
    """Async generator for streaming chat messages."""
    deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

    message_pairs = "\n\n".join(format_message_pairs(history, 3))
    logger.info(f"Message pairs: {message_pairs}")
    last_response = f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""

    user_message = f"{last_response}{deps.get_user_message()}"

    # Run moderation with tracing
    moderation_data = await _run_moderation(user_message, session_id)
    logger.info(f"Moderation data: {moderation_data}")
    deps.update_moderation_str(str(moderation_data))

    user_message = f"{last_response}{deps.get_user_message()}"

    # Trim and clean history
    trimmed_history = trim_history(history, max_tokens=64_000)
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
    trimmed_history = filter_thinking_from_history(trimmed_history)

    # Run main agent with tracing
    result = await _run_agrinet(
        user_message=deps.get_user_message(),
        trimmed_history=trimmed_history,
        deps=deps,
        session_id=session_id,
        user_id=user_id,
        query=query,
        moderation_category=moderation_data.category,
    )

    new_messages = result.new_messages()
    logger.info(f"Agent run complete for session {session_id}")

    yield result.output

    # Save updated message history
    clean_new_messages = filter_thinking_from_history(list(new_messages or []))
    messages = [*history, *clean_new_messages]
    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)

    langfuse.flush()


@observe(name="moderation", as_type="generation")
async def _run_moderation(user_message: str, session_id: str):
    """Run moderation agent and trace it in Langfuse."""
    langfuse_context.update_current_trace(
        session_id=session_id,
        input=user_message,              # ← fixes null input on trace
    )
    langfuse_context.update_current_observation(
        input=user_message,
        metadata={"session_id": session_id}
    )
    run = await moderation_agent.run(user_message)

    usage_data = run.usage()
    langfuse_context.update_current_observation(
        output=str(run.output),
        model=MODEL_NAME,
        usage={
            "input": usage_data.request_tokens or 0,
            "output": usage_data.response_tokens or 0,
            "unit": "TOKENS",
        }
    )
    langfuse_context.update_current_trace(
        output=str(run.output),          
    )
    return run.output


@observe(name="chat", as_type="generation")
async def _run_agrinet(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    query: str,
    moderation_category: str,
):
    """Run main agrinet agent and trace it in Langfuse."""
    langfuse_context.update_current_trace(
        session_id=session_id,
        user_id=user_id,
        input=query,
        tags=[moderation_category],
    )
    langfuse_context.update_current_observation(input=user_message)

    result = await agrinet_agent.run(
        user_prompt=user_message,
        message_history=trimmed_history,
        deps=deps,
    )

    usage_data = result.usage()
    langfuse_context.update_current_observation(
        output=result.output,
        model=MODEL_NAME,
        usage={
            "input": usage_data.request_tokens or 0,
            "output": usage_data.response_tokens or 0,
            "unit": "TOKENS",
        }
    )
    langfuse_context.update_current_trace(
        output=result.output,            
    )
    return result