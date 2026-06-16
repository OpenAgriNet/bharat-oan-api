import os
from copy import deepcopy
from typing import AsyncGenerator, Optional

from fastapi import BackgroundTasks

from agents.agrinet import agrinet_agent
from agents.models import LANGFUSE_AGRINET_MODEL_NAME, LANGFUSE_MODERATION_MODEL_NAME
from agents.moderation import moderation_agent
from helpers.langfuse_trace_schema import (
    AGENT_MODERATION,
    AGENT_VISTAAR,
    chat_trace_metadata_strings,
)
from helpers.langfuse_tracing import lf_update_current_observation, lf_update_current_span
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_answer_event,
    create_chat_error_event,
    create_chat_question_event,
    create_frontend_compatible_item_batch,
)
from helpers.utils import get_logger
from app.config import settings
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
    is_image_analysis: bool = False,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
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

    lf_env = settings.langfuse_tracing_environment
    trace_meta = chat_trace_metadata_strings(
        source_lang=source_lang,
        target_lang=target_lang,
        environment=lf_env,
        channel=channel,
        query=query,
    )
    trace_tags = [f"env:{lf_env}", f"channel:{channel}"]
    for model_name in dict.fromkeys(
        (LANGFUSE_AGRINET_MODEL_NAME, LANGFUSE_MODERATION_MODEL_NAME)
    ):
        trace_tags.append(f"model:{model_name}")
    with propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        metadata=trace_meta,
        tags=trace_tags,
        trace_name=CHAT_TRACE_NAME,
    ):
        try:
            lf_update_current_span(input=query)

            deps = FarmerContext(
                query=query,
                lang_code=target_lang,
                session_id=session_id,
                latitude=latitude,
                longitude=longitude,
            )

            message_pairs = "\n\n".join(format_message_pairs(history, 3))
            logger.info(f"Message pairs: {message_pairs}")
            last_response = (
                f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
            )

            def build_user_message() -> str:
                base_user_message = deps.get_user_message()
                if is_image_analysis:
                    base_user_message = (
                        f"[USER UPLOADED A CROP IMAGE]\n\n"
                        f"{base_user_message}\n\n"
                        "INSTRUCTION: Direct image-based pest or disease analysis is not available in this backend. "
                        "Do not claim that you inspected the image or identified anything from the photo. "
                        "Do not mention internal image URLs or backend implementation details. "
                        "Briefly tell the farmer that image analysis is unavailable right now, then ask them to describe the crop, visible symptoms, affected plant part, and location in text so you can help using the available tools."
                    )
                return f"{last_response}{base_user_message}"

            user_message = build_user_message()

            moderation_data = await _run_moderation(user_message, session_id)
            logger.info(f"Moderation data: {moderation_data}")
            deps.update_moderation_str(str(moderation_data))
            user_message = build_user_message()

            trimmed_history = trim_history(history, max_tokens=64_000)
            logger.info(f"Trimmed history length: {len(trimmed_history)} messages")
            trimmed_history = filter_thinking_from_history(trimmed_history)

            with propagate_attributes(tags=[moderation_data.category]):
                result = await _run_agrinet(
                    user_message=user_message,
                    trimmed_history=trimmed_history,
                    deps=deps,
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    moderation_category=moderation_data.category,
                )

            new_messages = result.new_messages()
            logger.info(f"Agent run complete for session {session_id}")

            output_text = result.output

            lf_update_current_span(output=output_text)

            answer_event = create_chat_answer_event(
                current_user=telemetry_user,
                qid=telemetry_qid,
                question_text=query,
                answer_text=output_text,
                session_id=session_id,
            )
            background_tasks.add_task(
                send_telemetry,
                TelemetryRequest(events=create_frontend_compatible_item_batch(answer_event)).model_dump(),
            )

            yield output_text

            clean_new_messages = filter_thinking_from_history(list(new_messages or []))
            clean_new_messages = _replace_last_text_output(clean_new_messages, output_text)
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


@observe(name=AGENT_MODERATION, as_type="agent")
async def _run_moderation(user_message: str, session_id: str):
    """Run moderation agent and trace it in Langfuse."""
    lf_update_current_observation(
        input=user_message,
        model=LANGFUSE_MODERATION_MODEL_NAME,
        metadata={"session_id": session_id},
    )

    run = await moderation_agent.run(user_message)

    usage_data = run.usage()
    lf_update_current_observation(
        output=str(run.output),
        model=LANGFUSE_MODERATION_MODEL_NAME,
        input_tokens=usage_data.input_tokens or 0,
        output_tokens=usage_data.output_tokens or 0,
        metadata={"session_id": session_id},
    )
    return run.output


def _replace_last_text_output(messages: list, output_text: str) -> list:
    """Store the same post-processed response that the farmer received."""
    if not messages or not output_text:
        return messages

    copied = deepcopy(messages)
    for message in reversed(copied):
        for part in reversed(getattr(message, "parts", []) or []):
            if getattr(part, "part_kind", "") == "text" and hasattr(part, "content"):
                part.content = output_text
                return copied
    return copied


@observe(name=AGENT_VISTAAR, as_type="agent")
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
        model=LANGFUSE_AGRINET_MODEL_NAME,
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
        model=LANGFUSE_AGRINET_MODEL_NAME,
        input_tokens=usage_data.input_tokens or 0,
        output_tokens=usage_data.output_tokens or 0,
        metadata={
            "session_id": session_id,
            "user_id": user_id,
            "moderation_category": moderation_category,
        },
    )
    return result
