import time
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
    extract_final_text,
)
from app.tasks.telemetry import send_telemetry
from app.tasks.tool_tracker import ToolUsageTracker
from agents.deps import FarmerContext

logger = get_logger(__name__)


async def stream_chat_messages(
    query: str,
    session_id: str,
    source_lang: str,
    target_lang: str,
    user_id: str,
    history: list,
    background_tasks: BackgroundTasks,
) -> AsyncGenerator[str, None]:
    """Async generator that streams the agrinet agent response to the caller."""

    start_time = time.time()

    deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

    # Build conversation context prefix
    message_pairs = "\n\n".join(format_message_pairs(history, 3))
    logger.info(f"Message pairs: {message_pairs}")
    last_response = (
        f"**Conversation**\n\n{message_pairs}\n\n---\n\n"
        if message_pairs
        else ""
    )

    user_message = f"{last_response}{deps.get_user_message()}"

    moderation_run  = await moderation_agent.run(user_message)
    moderation_data = moderation_run.output
    logger.info(f"Moderation data: {moderation_data}")
    deps.update_moderation_str(str(moderation_data))

    # Rebuild user_message after moderation context is injected into deps
    user_message = f"{last_response}{deps.get_user_message()}"

    trimmed_history = trim_history(history, max_tokens=64_000)
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
    trimmed_history = filter_thinking_from_history(trimmed_history)

    # Stream the agent response, tracking tool usage via ToolUsageTracker
    tracker = ToolUsageTracker()

    async for event in agrinet_agent.run_stream_events(
        user_prompt=deps.get_user_message(),
        message_history=trimmed_history,
        deps=deps,
    ):
        delta = tracker.process_event(event)
        if delta:
            yield delta

    logger.info(f"Streaming complete for session {session_id}")

    # Post-processing
    clean_new_messages = filter_thinking_from_history(list(tracker.new_messages or []))
    messages = [*history, *clean_new_messages]
    final_output = extract_final_text(clean_new_messages)

    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)

    total_latency = time.time() - start_time
    telemetry_data = {
        "session_id": session_id,
        "user_id": user_id,
        "query": query,
        "responce": final_output,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "tool_usage": tracker.as_list(),
        "moderation_category": moderation_data.category,
        "total_latency_seconds": total_latency,
    }

    logger.info(f"Telemetry data: {telemetry_data}")
    try:
        result = await send_telemetry(telemetry_data)
        logger.info(f"Telemetry result: {result}")
    except Exception as e:
        logger.error(f"Failed to send telemetry: {e}")
