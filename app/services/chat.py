import asyncio
import math
import os
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator, Awaitable, Generic, TypeVar

from fastapi import BackgroundTasks
from pydantic_ai import TextPartDelta
from pydantic_ai.messages import TextPart

from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.langfuse_trace_schema import (
    AGENT_MODERATION,
    AGENT_VISTAAR,
    chat_trace_metadata_strings,
)
from helpers.langfuse_helper import get_langfuse_tracing_environment
from helpers.langfuse_tracing import lf_set_trace_io, lf_update_current_observation
from helpers.utils import get_logger
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
from agents.deps import FarmerContext
from langfuse import get_client, observe, propagate_attributes


logger = get_logger(__name__)

MODEL_NAME = (
    os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    or os.getenv("LLM_AGRINET_MODEL_NAME")
    or os.getenv("LLM_MODEL_NAME")
)

CHAT_TRACE_NAME = (
    os.getenv("LANGFUSE_TRACE_NAME")
    or os.getenv("LANGFUSE_TRACE_ROOT_NAME")
    or "bharat-vistaar-chat"
)
CHAT_CHAIN_SPAN_NAME = "chain.chat"
DEFAULT_SSE_KEEPALIVE_INTERVAL_S = 3.0
SSE_KEEPALIVE = ": keep-alive\n\n"

T = TypeVar("T")


@dataclass(frozen=True)
class _AwaitedResult(Generic[T]):
    value: T


def _parse_sse_keepalive_interval(raw_value: str | None) -> float:
    if not raw_value:
        return DEFAULT_SSE_KEEPALIVE_INTERVAL_S

    try:
        interval_s = float(raw_value)
    except ValueError:
        logger.warning(
            "Invalid CHAT_SSE_KEEPALIVE_INTERVAL_S=%r; using default %.1fs",
            raw_value,
            DEFAULT_SSE_KEEPALIVE_INTERVAL_S,
        )
        return DEFAULT_SSE_KEEPALIVE_INTERVAL_S

    if not math.isfinite(interval_s) or interval_s <= 0:
        logger.warning(
            "CHAT_SSE_KEEPALIVE_INTERVAL_S must be a positive finite number; "
            "got %r, using default %.1fs",
            raw_value,
            DEFAULT_SSE_KEEPALIVE_INTERVAL_S,
        )
        return DEFAULT_SSE_KEEPALIVE_INTERVAL_S

    return interval_s


SSE_KEEPALIVE_INTERVAL_S = _parse_sse_keepalive_interval(
    os.getenv("CHAT_SSE_KEEPALIVE_INTERVAL_S")
)


def _format_sse_data(data: str) -> str:
    """Format text as a valid SSE data frame, preserving multiline content."""
    normalized = data.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(f"data: {line}\n" for line in normalized.split("\n")) + "\n"


def _strip_thinking_chunk(chunk: str, inside_thinking: bool) -> tuple[str, bool]:
    if inside_thinking:
        end_idx = chunk.find("</think>")
        if end_idx < 0:
            return "", True
        chunk = chunk[end_idx + len("</think>") :]
        inside_thinking = False

    while "<think>" in chunk:
        start_idx = chunk.find("<think>")
        end_idx = chunk.find("</think>", start_idx)
        if end_idx < 0:
            return chunk[:start_idx], True
        chunk = chunk[:start_idx] + chunk[end_idx + len("</think>") :]

    return chunk, inside_thinking


async def _await_with_sse_keepalives(
    coro: Awaitable[T],
    *,
    interval_s: float = SSE_KEEPALIVE_INTERVAL_S,
) -> AsyncGenerator[str | _AwaitedResult[T], None]:
    """Yield SSE comment heartbeats until `coro` completes."""
    task = asyncio.create_task(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval_s)
            if task in done:
                yield _AwaitedResult(task.result())
                return
            yield SSE_KEEPALIVE
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        raise


async def _iter_with_sse_keepalives(
    items: AsyncIterator[T],
    *,
    interval_s: float = SSE_KEEPALIVE_INTERVAL_S,
) -> AsyncGenerator[str | _AwaitedResult[T], None]:
    iterator = items.__aiter__()
    while True:
        task = asyncio.create_task(anext(iterator))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval_s)
                if task in done:
                    try:
                        yield _AwaitedResult(task.result())
                    except StopAsyncIteration:
                        return
                    break
                yield SSE_KEEPALIVE
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            aclose = getattr(iterator, "aclose", None)
            if aclose:
                await aclose()
            raise


@observe(name=CHAT_CHAIN_SPAN_NAME, as_type="chain")
async def stream_chat_messages(
    query: str,
    session_id: str,
    source_lang: str,
    target_lang: str,
    user_id: str,
    history: list,
    background_tasks: BackgroundTasks,
    channel: str = "BharatVistaar",
) -> AsyncGenerator[str, None]:
    """Async generator for streaming chat messages."""
    lf_env = get_langfuse_tracing_environment()
    trace_meta = chat_trace_metadata_strings(
        source_lang=source_lang,
        target_lang=target_lang,
        environment=lf_env,
        channel=channel,
        query=query,
    )
    trace_tags = [f"env:{lf_env}", f"channel:{channel}"]
    if MODEL_NAME:
        trace_tags.append(f"model:{MODEL_NAME}")
    with propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        metadata=trace_meta,
        tags=trace_tags,
        trace_name=CHAT_TRACE_NAME,
    ):
        lf_set_trace_io(input=query)

        deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

        message_pairs = "\n\n".join(format_message_pairs(history, 3))
        logger.info(f"Message pairs: {message_pairs}")
        last_response = (
            f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
        )

        user_message = f"{last_response}{deps.get_user_message()}"

        yield SSE_KEEPALIVE

        moderation_data = None
        async for item in _await_with_sse_keepalives(
            _run_moderation(user_message, session_id),
        ):
            if isinstance(item, _AwaitedResult):
                moderation_data = item
            else:
                yield item
        logger.info(f"Moderation data: {moderation_data.value}")
        deps.update_moderation_str(str(moderation_data.value))

        user_message = f"{last_response}{deps.get_user_message()}"

        trimmed_history = trim_history(history, max_tokens=64_000)
        logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
        trimmed_history = filter_thinking_from_history(trimmed_history)

        result = None
        with propagate_attributes(tags=[moderation_data.value.category]):
            async for item in _stream_agrinet(
                user_message=deps.get_user_message(),
                trimmed_history=trimmed_history,
                deps=deps,
                session_id=session_id,
                user_id=user_id,
                query=query,
                moderation_category=moderation_data.value.category,
            ):
                if isinstance(item, _AwaitedResult):
                    result = item.value
                else:
                    yield item

        if result is None:
            raise RuntimeError("Agrinet stream completed without a final result")

        new_messages = result.new_messages()
        logger.info(f"Agent run complete for session {session_id}")

        lf_set_trace_io(output=result.output)

        clean_new_messages = filter_thinking_from_history(list(new_messages or []))
        messages = [*history, *clean_new_messages]
        logger.info(
            f"Updating message history for session {session_id} with {len(messages)} messages"
        )
        await update_message_history(session_id, messages)

        get_client().flush()


@observe(name=AGENT_MODERATION, as_type="generation")
async def _run_moderation(user_message: str, session_id: str):
    """Run moderation agent and trace it in Langfuse."""
    lf_set_trace_io(input=user_message)
    lf_update_current_observation(
        input=user_message,
        metadata={"session_id": session_id},
    )

    run = await moderation_agent.run(user_message)

    usage_data = run.usage()
    lf_update_current_observation(
        output=str(run.output),
        model=MODEL_NAME,
        request_tokens=usage_data.request_tokens or 0,
        response_tokens=usage_data.response_tokens or 0,
        metadata={},
    )
    lf_set_trace_io(output=str(run.output))
    return run.output


@observe(name=AGENT_VISTAAR, as_type="generation")
async def _stream_agrinet(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    query: str,
    moderation_category: str,
) -> AsyncGenerator[str | _AwaitedResult, None]:
    """Stream main agrinet agent text deltas and trace the final result."""
    lf_update_current_observation(
        input=user_message,
        metadata={
            "session_id": session_id,
            "user_id": user_id,
            "moderation_category": moderation_category,
        },
    )

    result = None
    final_result_found = False
    pending_text_chunks: list[str] = []
    inside_leaked_think = False

    async def _yield_sse_text(chunk: str):
        nonlocal inside_leaked_think
        chunk, inside_leaked_think = _strip_thinking_chunk(
            chunk,
            inside_leaked_think,
        )
        if chunk:
            yield _format_sse_data(chunk)

    async for item in _iter_with_sse_keepalives(
        agrinet_agent.run_stream_events(
            user_prompt=user_message,
            message_history=trimmed_history,
            deps=deps,
        ),
    ):
        if not isinstance(item, _AwaitedResult):
            yield item
            continue

        event = item.value
        event_kind = getattr(event, "event_kind", "")

        if event_kind == "agent_run_result":
            result = event.result
            continue

        if event_kind == "final_result":
            final_result_found = True
            for chunk in pending_text_chunks:
                async for sse_chunk in _yield_sse_text(chunk):
                    yield sse_chunk
            pending_text_chunks.clear()
            continue

        if event_kind == "function_tool_result":
            final_result_found = False
            pending_text_chunks.clear()
            inside_leaked_think = False
            continue

        if event_kind == "part_start":
            part = getattr(event, "part", None)
            if isinstance(part, TextPart):
                chunk = part.content or ""
                if final_result_found:
                    async for sse_chunk in _yield_sse_text(chunk):
                        yield sse_chunk
                elif chunk:
                    pending_text_chunks.append(chunk)
            continue

        if event_kind == "part_delta":
            delta = getattr(event, "delta", None)
            if isinstance(delta, TextPartDelta):
                chunk = delta.content_delta or ""
                if final_result_found:
                    async for sse_chunk in _yield_sse_text(chunk):
                        yield sse_chunk
                elif chunk:
                    pending_text_chunks.append(chunk)

    if result is None:
        raise RuntimeError("Agrinet agent stream ended without a final result")

    usage_data = result.usage()
    lf_update_current_observation(
        output=result.output,
        model=MODEL_NAME,
        request_tokens=usage_data.request_tokens or 0,
        response_tokens=usage_data.response_tokens or 0,
        metadata={},
    )
    lf_set_trace_io(output=result.output)
    yield _AwaitedResult(result)
