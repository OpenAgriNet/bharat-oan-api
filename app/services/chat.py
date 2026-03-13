import time
from contextlib import nullcontext
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
from pydantic_ai.messages import TextPart, ThinkingPart, RetryPromptPart
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
            as_type="agent",
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

            moderation_span_ctx = (
                langfuse.start_as_current_observation(
                    as_type="span",
                    name="moderation-agent",
                    input=user_message,
                )
                if langfuse
                else nullcontext()
            )

            with moderation_span_ctx as moderation_span:
                moderation_run = await moderation_agent.run(user_message)
                moderation_usage = moderation_run.usage() if moderation_run else {}
                logger.info(f"Moderation usage: {moderation_usage}")
                moderation_data = moderation_run.output
                logger.info(f"Moderation data: {moderation_data}")

                if moderation_span is not None:
                    try:
                        usage_dict = (
                            {
                                "input": getattr(moderation_usage, "request_tokens", None),
                                "output": getattr(moderation_usage, "response_tokens", None),
                                "total": getattr(moderation_usage, "total_tokens", None),
                            }
                            if moderation_usage
                            else None
                        )
                        moderation_span.update(
                            output=moderation_data.model_dump() if hasattr(moderation_data, "model_dump") else str(moderation_data),
                            metadata={"usage": usage_dict},
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update moderation span: {e}")

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

            agrinet_span_ctx = (
                langfuse.start_as_current_observation(
                    as_type="span",
                    name="agrinet-agent",
                    input=deps.get_user_message(),
                )
                if langfuse
                else nullcontext()
            )

            with agrinet_span_ctx as agrinet_span:
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
                        tool_call_id = getattr(event.part, 'tool_call_id', None)
                        logger.info(f"Tool call: {tool_name} (id={tool_call_id})")
                        if tool_name not in ('final_result', 'json'):
                            agrinet_tools.append({
                                'tool_name': tool_name,
                                'args': getattr(event.part, 'args', None),
                                'tool_call_id': tool_call_id,
                                'status': 'pending',
                                'result': None,
                                'error': None,
                            })

                    elif kind == 'function_tool_result':
                        result_part = getattr(event, 'result', None)
                        tool_call_id = getattr(result_part, 'tool_call_id', None)
                        tool_name_result = getattr(result_part, 'tool_name', 'unknown')
                        content = getattr(result_part, 'content', None)
                        is_error = isinstance(result_part, RetryPromptPart)
                        logger.info(
                            f"Tool result: {tool_name_result} (id={tool_call_id}) "
                            f"status={'error' if is_error else 'success'}"
                        )
                        for tool in agrinet_tools:
                            if tool.get('tool_call_id') == tool_call_id:
                                if is_error:
                                    tool['status'] = 'error'
                                    tool['error'] = str(content)[:1000] if content is not None else None
                                else:
                                    tool['status'] = 'success'
                                    tool['result'] = str(content)[:1000] if content is not None else None
                                break
                        final_result_found = False  # Reset for next model turn

                    elif kind == 'agent_run_result':
                        agrinet_result_obj = event.result
                        new_messages = event.result.new_messages()

                logger.info(f"Streaming complete for session {session_id}")

                # Update agrinet span with output and usage
                if agrinet_span is not None:
                    try:
                        agrinet_usage = agrinet_result_obj.usage() if agrinet_result_obj else None
                        usage_dict = (
                            {
                                "input": getattr(agrinet_usage, "request_tokens", None),
                                "output": getattr(agrinet_usage, "response_tokens", None),
                                "total": getattr(agrinet_usage, "total_tokens", None),
                            }
                            if agrinet_usage
                            else None
                        )
                        agrinet_output = ""
                        for msg in reversed(list(new_messages or [])):
                            for part in getattr(msg, "parts", []):
                                if isinstance(part, TextPart):
                                    agrinet_output = part.content
                                    break
                            if agrinet_output:
                                break
                        agrinet_span.update(
                            output=agrinet_output,
                            metadata={"usage": usage_dict},
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update agrinet span: {e}")

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
            logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
            await update_message_history(session_id, messages)  
            total_latency = time.time() - start_time
            tool_telemetry = [
                {
                    "tool_name": t["tool_name"],
                    "args": t.get("args"),
                    "status": t.get("status", "unknown"),
                    "result": t.get("result"),
                    "error": t.get("error"),
                }
                for t in agrinet_tools
            ]

            telemetry_data = {
                "session_id": session_id,
                "user_id": user_id,
                "tool_usage": tool_telemetry,
                "moderation_category": moderation_data.category,
                "total_latency_seconds": total_latency
            }
            await observability.log_telemetry(telemetry_data)
            # await observability.send_telemetry(telemetry_data)

           