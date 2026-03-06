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

<<<<<<< HEAD
<<<<<<< HEAD
=======
>>>>>>> 4025315 (version-1 with langfuse fix)
    logger.info(f"Telemetry data: {telemetry_data}")
    try:
        result = await send_telemetry(telemetry_data)
        logger.info(f"Telemetry result: {result}")
    except Exception as e:
<<<<<<< HEAD
        logger.error(f"Failed to send telemetry: {e}")
=======
    root_span_ctx = (
        langfuse.start_as_current_observation(
            as_type="generation",
            name="BharatVistar-AGENTS",
            input={"query": query, "session_id": session_id_safe},
        )
        if langfuse
        else nullcontext()
    )

    propagate_ctx = (
        propagate_attributes(
            user_id=(user_id or "anonymous")[:200],
            session_id=session_id_safe,
            metadata=langfuse_metadata,
            tags=langfuse_tags,
            version="1.0",
        )
        if propagate_attributes
        else nullcontext()
    )

    with root_span_ctx as root_span:
        with propagate_ctx:
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

            # Include conversation in the user message so the agent always sees prior context
            # (in addition to message_history). This reinforces conversation awareness.
            user_message = f"{last_response}{deps.get_user_message()}"

            # Run the main agent
            trimmed_history = trim_history(history, max_tokens=40_000)
            logger.info(f"Trimmed history length: {len(trimmed_history)} messages")

            # Strip ThinkingPart from history so pydantic-ai doesn't wrap them
            # back into <think> tags when sending to vLLM (prevents "Unknown role"
            # errors and avoids leaking reasoning into the conversation context).
            trimmed_history = filter_thinking_from_history(trimmed_history)

            new_messages = None
            final_result_found = False

            async for event in agrinet_agent.run_stream_events(
                user_prompt=deps.get_user_message(),
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

            # ------------------------------------------------------------------
            # Post-processing — runs AFTER streaming is complete
            # ------------------------------------------------------------------
            if not new_messages:
                new_messages = []

            # Strip thinking parts before persisting so they don't accumulate
            # in the cache and get sent back to vLLM on subsequent turns.
            clean_new_messages = filter_thinking_from_history(list(new_messages))
            messages = [*history, *clean_new_messages]

            # Extract final text output to close the root span with output
            final_output = ""
            for msg in reversed(clean_new_messages):
                for part in getattr(msg, "parts", []):
                    if isinstance(part, TextPart):
                        final_output = part.content
                        break
                if final_output:
                    break

            # Close root span with the final response
            if root_span is not None:
                try:
                    root_span.update(output={"response": final_output})
                except Exception as e:
                    logger.warning(f"Failed to update root span output: {e}")

            logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
            await update_message_history(session_id, messages)
>>>>>>> 873e131 (fix(chat): update observation name for langfuse integration)
=======
        logger.error(f"Failed to send telemetry: {e}")
>>>>>>> 4025315 (version-1 with langfuse fix)
