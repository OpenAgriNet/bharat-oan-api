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
from app.services.observability_service import ObservabilityService

logger = get_logger(__name__)
observability = ObservabilityService()

try:
    from langfuse import get_client, propagate_attributes
    langfuse = get_client()
except ImportError:
    langfuse = None
    propagate_attributes = None


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

    start_time = time.time()
    session_id_safe = (session_id or "")[:200]
    langfuse_metadata = {
        "source_lang": (source_lang or "unknown").lower()[:200],
        "target_lang": (target_lang or "unknown").lower()[:200],
        "user_id": (user_id or "anonymous")[:200],
    }

    root_span_ctx = (
        langfuse.start_as_current_observation(
            as_type="span",
            name="BharatVistar-AGENTS",
            input=query,
        )
        if langfuse
        else nullcontext()
    )

    propagate_ctx = (
        propagate_attributes(
            user_id=(user_id or "anonymous")[:200],
            session_id=session_id_safe,
            metadata=langfuse_metadata,
            version="1.0",
        )
        if propagate_attributes
        else nullcontext()
    )

    with root_span_ctx as root_span:
        with propagate_ctx:
            content_id = f"query_{session_id}_{len(history)//2 + 1}"

            deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

            message_pairs = "\n\n".join(format_message_pairs(history, 3))
            logger.info(f"Message pairs: {message_pairs}")
            if message_pairs:
                last_response = f"**Conversation**\n\n{message_pairs}\n\n---\n\n"
            else:
                last_response = ""

            user_message   = f"{last_response}{deps.get_user_message()}"
            moderation_run = await moderation_agent.run(user_message)
            moderation_usage = moderation_run.usage() if moderation_run else {}
            logger.info(f"Moderation usage: {moderation_usage}") 
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
            agrinet_tools = []
            agrinet_result_obj = None

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
                        pass  # Collected from new_messages after stream
                    elif isinstance(event.delta, TextPartDelta):
                        if final_result_found and event.delta.content_delta:
                            yield event.delta.content_delta

                elif kind == 'final_result':
                    logger.info("[Result] The model started producing a final result")
                    final_result_found = True

                elif kind == 'function_tool_call':
                    tool_name = event.part.tool_name
                    logger.info(f"Tool call: {tool_name}")
                    if tool_name not in ('final_result', 'json'):
                        agrinet_tools.append({
                            'tool_name': tool_name,
                            'args': getattr(event.part, 'args', None),
                        })

                elif kind == 'function_tool_result':
                    logger.info("Tool result received")
                    final_result_found = False  # Reset for next model turn

                elif kind == 'agent_run_result':
                    agrinet_result_obj = event.result
                    new_messages = event.result.new_messages()

            logger.info(f"Streaming complete for session {session_id}")

            # ------------------------------------------------------------------
            # Post-processing — runs AFTER streaming is complete
            # ------------------------------------------------------------------
            if not new_messages:
                new_messages = []

            # Extract agrinet thinking from new_messages before filtering
            

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

            # Close root span with the final response and set tags (tool names now available)
            if root_span is not None:
                tool_tags = [f"tool:{t['tool_name']}" for t in agrinet_tools]
                try:
                    root_span.update(output=final_output)
                    root_span.update_trace(tags=[
                        moderation_data.category,
                        f"source_lang:{source_lang}",
                        f"target_lang:{target_lang}",
                        *tool_tags
                    ])
                except Exception as e:
                    logger.warning(f"Failed to update root span output: {e}")

            # Build and log telemetry dict
          
            agrinet_usage = agrinet_result_obj.usage() if agrinet_result_obj else None

            mod_in = getattr(moderation_usage, 'input_tokens', 0) or 0
            mod_out = getattr(moderation_usage, 'output_tokens', 0) or 0
            agri_in = (getattr(agrinet_usage, 'input_tokens', 0) or 0) if agrinet_usage else 0
            agri_out = (getattr(agrinet_usage, 'output_tokens', 0) or 0) if agrinet_usage else 0
            logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
            await update_message_history(session_id, messages)  
            total_latency = time.time() - start_time

            telemetry_data = {
                "session_id": session_id,
                "user_id": user_id,
                "total_input_tokens": mod_in + agri_in,
                "total_output_tokens": mod_out + agri_out,
                "tools_used": [t['tool_name'] for t in agrinet_tools],
                "total_latency_seconds": total_latency
            }

            await observability.log_telemetry(telemetry_data)
            await observability.send_telemetry(telemetry_data)

           