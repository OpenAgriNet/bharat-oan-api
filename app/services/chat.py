from typing import AsyncGenerator
from fastapi import BackgroundTasks
from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.utils import get_logger
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
# from app.tasks.suggestions import create_suggestions  # Commented out: suggestion agent disabled
from agents.deps import FarmerContext

logger = get_logger(__name__)

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
    # Generate a unique content ID for this query
    content_id = f"query_{session_id}_{len(history)//2 + 1}"
       
    deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

    message_pairs = "\n\n".join(format_message_pairs(history, 3))
    logger.info(f"Message pairs: {message_pairs}")
    if message_pairs:
        last_response = f"**Conversation**\n\n{message_pairs}\n\n---\n\n"
    else:
        last_response = ""
    
    user_message    = f"{last_response}{deps.get_user_message()}"
    moderation_run  = await moderation_agent.run(user_message)
    moderation_data = moderation_run.output
    logger.info(f"Moderation data: {moderation_data}")


    deps.update_moderation_str(str(moderation_data))

    # Include conversation in the user message so the agent always sees prior context
    # (in addition to message_history). This reinforces conversation awareness.
    user_message = f"{last_response}{deps.get_user_message()}"

    # Run the main agent
    trimmed_history = trim_history(
        history,
        max_tokens=64_000
    )
    
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")

    # Strip ThinkingPart from history so pydantic-ai doesn't wrap them
    # back into <think> tags when sending to vLLM (prevents "Unknown role"
    # errors and avoids leaking reasoning into the conversation context).
    trimmed_history = filter_thinking_from_history(trimmed_history)

    result = await agrinet_agent.run(
        user_prompt=deps.get_user_message(),
        message_history=trimmed_history,
        deps=deps,
    )

    new_messages = result.new_messages()
    logger.info(f"Agent run complete for session {session_id}")

    yield result.output

    # Post-processing happens AFTER streaming is complete.
    # Strip thinking parts before persisting so they don't accumulate
    # in the cache and get sent back to vLLM on subsequent turns.
    if not new_messages:
        new_messages = []
    clean_new_messages = filter_thinking_from_history(list(new_messages))
    messages = [*history, *clean_new_messages]

    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)
