import time
from contextlib import nullcontext
from typing import AsyncGenerator

from fastapi import BackgroundTasks
from pydantic_ai import TextPartDelta
from pydantic_ai.messages import TextPart, ThinkingPart, RetryPromptPart

from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.utils import get_logger
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
from agents.deps import FarmerContext
from app.services.observability_service import ObservabilityService

logger = get_logger(__name__)
observability = ObservabilityService()
from langfuse import get_client, propagate_attributes

langfuse = get_client()


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

           
            moderation_data = await _run_moderation(
                user_message=user_message,
                session_id=session_id,
            )
            logger.info(f"Moderation data: {moderation_data}")
            deps.update_moderation_str(str(moderation_data))

            # Rebuild user_message after moderation context is injected into deps
            user_message = f"{last_response}{deps.get_user_message()}"

            
            trimmed_history = trim_history(history, max_tokens=40_000)
            logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
            trimmed_history = filter_thinking_from_history(trimmed_history)

          
            stream_state = {}
            async for chunk in _stream_agrinet(
                user_message=deps.get_user_message(),
                trimmed_history=trimmed_history,
                deps=deps,
                session_id=session_id,
                stream_state=stream_state,
            ):
                yield chunk

            agrinet_tools = stream_state.get("agrinet_tools", [])
            new_messages = stream_state.get("new_messages")


            clean_new_messages = filter_thinking_from_history(list(new_messages or []))
            messages = [*history, *clean_new_messages]

            final_output = _extract_final_text(clean_new_messages)

            # Close root span
            if root_span is not None:
                tool_tags = [f"tool:{t['tool_name']}" for t in agrinet_tools]
                try:
                    root_span.update(output=final_output)
                    root_span.update_trace(tags=[
                        moderation_data.category,
                        f"source_lang:{source_lang}",
                        f"target_lang:{target_lang}",
                        *tool_tags,
                    ])
                except Exception as e:
                    logger.warning(f"Failed to update root span output: {e}")

            logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
            await update_message_history(session_id, messages)

            total_latency = time.time() - start_time
            await _log_telemetry(
                session_id=session_id,
                user_id=user_id,
                agrinet_tools=agrinet_tools,
                moderation_category=moderation_data.category,
                total_latency=total_latency,
            )



async def _run_moderation(user_message: str, session_id: str):
    """Run the moderation agent, wrapped in its own Langfuse span."""

    span_ctx = (
        langfuse.start_as_current_observation(
            as_type="span",
            name="moderation-agent",
            input=user_message,
        )
        if langfuse
        else nullcontext()
    )

    with span_ctx as span:
        moderation_run = await moderation_agent.run(user_message)
        moderation_usage = moderation_run.usage() if moderation_run else {}
        logger.info(f"Moderation usage: {moderation_usage}")

        moderation_data = moderation_run.output

        if span is not None:
            try:
                span.update(
                    output=(
                        moderation_data.model_dump()
                        if hasattr(moderation_data, "model_dump")
                        else str(moderation_data)
                    ),
                    metadata={
                        "usage": _usage_dict(moderation_usage),
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to update moderation span: {e}")

    return moderation_data


async def _stream_agrinet(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    stream_state: dict,
) -> AsyncGenerator[str, None]:
    """
    Stream the agrinet agent, yielding text deltas to the caller.

    Final stream artifacts are written into stream_state:
        stream_state["agrinet_tools"]
        stream_state["new_messages"]
        stream_state["agrinet_result_obj"]
    """

    span_ctx = (
        langfuse.start_as_current_observation(
            as_type="span",
            name="agrinet-agent",
            input=user_message,
        )
        if langfuse
        else nullcontext()
    )

    agrinet_tools = []
    agrinet_result_obj = None
    new_messages = None
    final_result_found = False

    with span_ctx as span:
        async for event in agrinet_agent.run_stream_events(
            user_prompt=user_message,
            message_history=trimmed_history,
            deps=deps,
        ):
            kind = getattr(event, "event_kind", "")

            if kind == "part_start":
                if isinstance(event.part, ThinkingPart):
                    logger.info("Reasoning part started (not streamed to user)")

            elif kind == "part_delta":
                if isinstance(event.delta, TextPartDelta):
                    if final_result_found and event.delta.content_delta:
                        yield event.delta.content_delta
                # ThinkingPartDelta: collected from new_messages after stream

            elif kind == "final_result":
                logger.info("[Result] The model started producing a final result")
                final_result_found = True

            elif kind == "function_tool_call":
                tool_name = event.part.tool_name
                tool_call_id = getattr(event.part, "tool_call_id", None)
                logger.info(f"Tool call: {tool_name} (id={tool_call_id})")
                if tool_name not in ("final_result", "json"):
                    agrinet_tools.append({
                        "tool_name": tool_name,
                        "args": getattr(event.part, "args", None),
                        "tool_call_id": tool_call_id,
                        "status": "pending",
                        "result": None,
                        "error": None,
                    })

            elif kind == "function_tool_result":
                result_part = getattr(event, "result", None)
                tool_call_id = getattr(result_part, "tool_call_id", None)
                tool_name_result = getattr(result_part, "tool_name", "unknown")
                content = getattr(result_part, "content", None)
                is_error = isinstance(result_part, RetryPromptPart)
                logger.info(
                    f"Tool result: {tool_name_result} (id={tool_call_id}) "
                    f"status={'error' if is_error else 'success'}"
                )
                for tool in agrinet_tools:
                    if tool.get("tool_call_id") == tool_call_id:
                        if is_error:
                            tool["status"] = "error"
                            tool["error"] = str(content)[:1000] if content is not None else None
                        else:
                            tool["status"] = "success"
                            tool["result"] = str(content)[:1000] if content is not None else None
                        break
                # Reset flag — model may produce another turn after tool result
                final_result_found = False

            elif kind == "agent_run_result":
                agrinet_result_obj = event.result
                new_messages = event.result.new_messages()

        logger.info(f"Streaming complete for session {session_id}")

        # Update agrinet span once streaming is done
        if span is not None:
            try:
                agrinet_usage = agrinet_result_obj.usage() if agrinet_result_obj else None
                agrinet_output = _extract_final_text(list(new_messages or []))
                span.update(
                    output=agrinet_output,
                    metadata={"usage": _usage_dict(agrinet_usage)},
                )
            except Exception as e:
                logger.warning(f"Failed to update agrinet span: {e}")

    stream_state["agrinet_result_obj"] = agrinet_result_obj
    stream_state["agrinet_tools"] = agrinet_tools
    stream_state["new_messages"] = new_messages

def _usage_dict(usage) -> dict | None:
    """Normalise a pydantic-ai Usage object into a plain dict for Langfuse."""
    if not usage:
        return None
    return {
        "input": getattr(usage, "request_tokens", None),
        "output": getattr(usage, "response_tokens", None),
        "total": getattr(usage, "total_tokens", None),
    }


def _extract_final_text(messages: list) -> str:
    """Return the last TextPart content from a message list."""
    for msg in reversed(messages):
        for part in getattr(msg, "parts", []):
            if isinstance(part, TextPart):
                return part.content
    return ""


async def _log_telemetry(
    session_id: str,
    user_id: str,
    agrinet_tools: list,
    moderation_category: str,
    total_latency: float,
) -> None:
    """Build the telemetry payload and hand it to ObservabilityService."""
    tool_telemetry = [
        {
            "tool_name": t["tool_name"],
            "args": t.get("args"),
            "status": t.get("status", "unknown"),
            "result": t.get("result")[:50] + "..." if t.get("result") is not None else None,
            "error": t.get("error"),
        }
        for t in agrinet_tools
    ]

    telemetry_data = {
        "session_id": session_id,
        "user_id": user_id,
        "tool_usage": tool_telemetry,
        "moderation_category": moderation_category,
        "total_latency_seconds": total_latency,
    }

    await observability.log_telemetry(telemetry_data)
    # await observability.send_telemetry(telemetry_data)