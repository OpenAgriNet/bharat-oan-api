import os
import math
import asyncio
import contextlib
from typing import AsyncGenerator, Optional
from dataclasses import dataclass
from typing import Awaitable, Generic, TypeVar

from fastapi import BackgroundTasks

from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.langfuse_trace_schema import (
    AGENT_MODERATION,
    AGENT_VISTAAR,
    chat_trace_metadata_strings,
)
from helpers.langfuse_helper import get_langfuse_tracing_environment
from helpers.langfuse_tracing import lf_set_trace_io, lf_update_current_observation
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_answer_event,
    create_chat_error_event,
    create_chat_question_event,
    create_frontend_compatible_item_batch,
)
from helpers.utils import get_logger
from app.tasks.telemetry import send_telemetry
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
from agents.deps import FarmerContext
from langfuse import get_client, observe, propagate_attributes


logger = get_logger(__name__)
T = TypeVar("T")

SSE_KEEPALIVE = "SSE_KEEPALIVE"


def _resolve_keepalive_interval_s() -> float:
    raw_value = os.getenv("CHAT_SSE_KEEPALIVE_INTERVAL_S", "3")
    try:
        interval = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid CHAT_SSE_KEEPALIVE_INTERVAL_S=%r; using 3.0 seconds", raw_value)
        return 3.0

    if not math.isfinite(interval) or interval <= 0:
        logger.warning("Non-positive CHAT_SSE_KEEPALIVE_INTERVAL_S=%r; using 3.0 seconds", raw_value)
        return 3.0

    return interval


CHAT_SSE_KEEPALIVE_INTERVAL_S = _resolve_keepalive_interval_s()


@dataclass(frozen=True)
class _AwaitedResult(Generic[T]):
    value: T


async def _wait_with_keepalive(awaitable: Awaitable[T]) -> AsyncGenerator[str | _AwaitedResult[T], None]:
    task = asyncio.create_task(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=CHAT_SSE_KEEPALIVE_INTERVAL_S)
            if task in done:
                yield _AwaitedResult(task.result())
                return

            yield SSE_KEEPALIVE
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
    qid: str = "",
    current_user: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """Async generator for streaming chat messages."""
    telemetry_user = current_user or {"channel": channel}
    telemetry_qid = qid or f"chat_{session_id}"
    question_event = create_chat_question_event(
        current_user=telemetry_user,
        qid=telemetry_qid,
        question_text=query,
        session_id=session_id,
    )
    background_tasks.add_task(
        send_telemetry,
        TelemetryRequest(events=create_frontend_compatible_item_batch(question_event)).model_dump(),
    )

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
        try:
            lf_set_trace_io(input=query)

            deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

            message_pairs = "\n\n".join(format_message_pairs(history, 3))
            logger.info(f"Message pairs: {message_pairs}")
            last_response = (
                f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
            )

            user_message = f"{last_response}{deps.get_user_message()}"

            async for item in _wait_with_keepalive(_run_moderation(user_message, session_id)):
                if item == SSE_KEEPALIVE:
                    yield SSE_KEEPALIVE
                    continue
                moderation_data = item.value

            logger.info(f"Moderation data: {moderation_data}")
            deps.update_moderation_str(str(moderation_data))

            user_message = f"{last_response}{deps.get_user_message()}"

            trimmed_history = trim_history(history, max_tokens=64_000)
            logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
            trimmed_history = filter_thinking_from_history(trimmed_history)

            with propagate_attributes(tags=[moderation_data.category]):
                async for item in _wait_with_keepalive(
                    _run_agrinet(
                        user_message=deps.get_user_message(),
                        trimmed_history=trimmed_history,
                        deps=deps,
                        session_id=session_id,
                        user_id=user_id,
                        query=query,
                        moderation_category=moderation_data.category,
                    )
                ):
                    if item == SSE_KEEPALIVE:
                        yield SSE_KEEPALIVE
                        continue
                    result = item.value

            new_messages = result.new_messages()
            logger.info(f"Agent run complete for session {session_id}")

            lf_set_trace_io(output=result.output)

            answer_event = create_chat_answer_event(
                current_user=telemetry_user,
                qid=telemetry_qid,
                question_text=query,
                answer_text=result.output,
                session_id=session_id,
            )
            background_tasks.add_task(
                send_telemetry,
                TelemetryRequest(events=create_frontend_compatible_item_batch(answer_event)).model_dump(),
            )

            yield result.output

            clean_new_messages = filter_thinking_from_history(list(new_messages or []))
            messages = [*history, *clean_new_messages]
            logger.info(
                f"Updating message history for session {session_id} with {len(messages)} messages"
            )
            await update_message_history(session_id, messages)

            get_client().flush()
        except Exception as exc:
            error_event = create_chat_error_event(
                current_user=telemetry_user,
                qid=telemetry_qid,
                session_id=session_id,
                error_text=f"{type(exc).__name__}: {exc}",
                question_text=query,
            )
            background_tasks.add_task(
                send_telemetry,
                TelemetryRequest(events=create_frontend_compatible_item_batch(error_event)).model_dump(),
            )
            raise


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
    lf_update_current_observation(
        input=user_message,
        metadata={
            "session_id": session_id,
            "user_id": user_id,
            "moderation_category": moderation_category,
        },
    )

    result = await agrinet_agent.run(
        user_prompt=user_message,
        message_history=trimmed_history,
        deps=deps,
    )

    usage_data = result.usage()
    lf_update_current_observation(
        output=result.output,
        model=MODEL_NAME,
        request_tokens=usage_data.request_tokens or 0,
        response_tokens=usage_data.response_tokens or 0,
        metadata={},
    )
    lf_set_trace_io(output=result.output)
    return result
