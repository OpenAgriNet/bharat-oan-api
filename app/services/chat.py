import os
from typing import AsyncGenerator

from fastapi import BackgroundTasks

from agents.agrinet import agrinet_agent
from agents.moderation import QueryModerationResult, moderation_agent
from helpers.langfuse_trace_schema import (
    AGENT_MODERATION,
    AGENT_VISTAAR,
    chat_trace_metadata_strings,
)
from helpers.langfuse_helper import get_langfuse_tracing_environment
from helpers.langfuse_tracing import lf_set_trace_io, lf_update_current_observation
from helpers.telemetry import TelemetryRequest, create_moderation_event
from helpers.utils import get_logger
from app.utils import (
    update_message_history,
    trim_history,
    format_message_pairs,
    filter_thinking_from_history,
)
from app.tasks.telemetry import send_telemetry
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


def _enqueue_moderation_telemetry(
    *,
    background_tasks: BackgroundTasks,
    query: str,
    session_id: str,
    user_id: str,
    moderation_data: QueryModerationResult,
) -> None:
    """Queue moderation telemetry without affecting the chat response path."""
    try:
        telemetry_event = create_moderation_event(
            question_text=query,
            moderation_type="CHAT_QUERY",
            content_id=session_id,
            session_id=session_id,
            content_type="chat_query",
            moderation_service="moderation_agent",
            flagged=moderation_data.category != "valid_agricultural",
            category=moderation_data.category,
            action=moderation_data.action,
            uid=user_id,
        )
        telemetry_data = TelemetryRequest(events=[telemetry_event]).model_dump()
        background_tasks.add_task(send_telemetry, telemetry_data)
    except Exception:
        logger.exception(
            "Failed to enqueue moderation telemetry | session_id=%s user_id=%s",
            session_id,
            user_id,
        )


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

        moderation_data = await _run_moderation(user_message, session_id)
        logger.info(f"Moderation data: {moderation_data}")
        _enqueue_moderation_telemetry(
            background_tasks=background_tasks,
            query=query,
            session_id=session_id,
            user_id=user_id,
            moderation_data=moderation_data,
        )
        deps.update_moderation_str(str(moderation_data))

        user_message = f"{last_response}{deps.get_user_message()}"

        trimmed_history = trim_history(history, max_tokens=64_000)
        logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
        trimmed_history = filter_thinking_from_history(trimmed_history)

        with propagate_attributes(tags=[moderation_data.category]):
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

        lf_set_trace_io(output=result.output)

        yield result.output

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
