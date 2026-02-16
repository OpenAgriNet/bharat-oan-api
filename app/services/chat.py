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
from pydantic_ai import (
    AgentRunResultEvent,
    FinalResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.messages import TextPart, ThinkingPart

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

    
    # Generate suggestions after moderation passes
    # Commented out: suggestion agent disabled
    # if moderation_data.category == "valid_agricultural":
    #     logger.info(f"Triggering suggestions generation for session {session_id}")
    #     try:
    #         background_tasks.add_task(create_suggestions, session_id, target_lang)
    #         logger.info("Successfully added suggestions task")
    #     except Exception as e:
    #         logger.error(f"Error adding suggestions task: {str(e)}")

    deps.update_moderation_str(str(moderation_data))

    user_message = deps.get_user_message()
    logger.info(f"Running agent with user message: {user_message}")

    # Run the main agent
    trimmed_history = trim_history(
        history,
        max_tokens=40_000
    )
    
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")

    # Strip ThinkingPart from history so pydantic-ai doesn't wrap them
    # back into <think> tags when sending to vLLM (prevents "Unknown role"
    # errors and avoids leaking reasoning into the conversation context).
    trimmed_history = filter_thinking_from_history(trimmed_history)

    new_messages = None
    final_result_found = False

    async for event in agrinet_agent.run_stream_events(
        user_prompt=user_message,
        message_history=trimmed_history,
        deps=deps
    ):
        kind = getattr(event, 'event_kind', '')

        if kind == 'part_start':
            if isinstance(event.part, ThinkingPart):
                logger.info("Reasoning part started (not streamed to user)")

        elif kind == 'part_delta':
            if isinstance(event.delta, ThinkingPartDelta):
                pass  # Don't stream reasoning to user
            elif isinstance(event.delta, TextPartDelta):
                # Only yield text deltas after FinalResultEvent
                if final_result_found and event.delta.content_delta:
                    yield event.delta.content_delta

        elif kind == 'final_result':
            logger.info("[Result] The model started producing a final result")
            final_result_found = True

        elif kind == 'function_tool_call':
            logger.info(f"Tool call: {event.part.tool_name}")

        elif kind == 'function_tool_result':
            logger.info("Tool result received")
            final_result_found = False  # Reset for next model turn

        elif kind == 'agent_run_result':
            new_messages = event.result.new_messages()

    logger.info(f"Streaming complete for session {session_id}")

    # Post-processing happens AFTER streaming is complete.
    # Strip thinking parts before persisting so they don't accumulate
    # in the cache and get sent back to vLLM on subsequent turns.
    if not new_messages:
        new_messages = []
    clean_new_messages = filter_thinking_from_history(list(new_messages))
    messages = [*history, *clean_new_messages]

    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)
