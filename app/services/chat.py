import asyncio
import os
import re
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, AsyncGenerator, Optional

from fastapi import BackgroundTasks
from langfuse import get_client, observe, propagate_attributes
from pydantic_ai import AgentRunResultEvent, FinalResultEvent, PartDeltaEvent, TextPartDelta
from pydantic_ai.models.openai import OpenAIChatModelSettings

from agents.agrinet import agrinet_agent
from agents.deps import FarmerContext
from agents.models import (
    LANGFUSE_MODERATION_MODEL_NAME,
    AgrinetRoute,
    get_agrinet_route_model,
    get_agrinet_route_model_name,
)
from agents.moderation import moderation_agent
from app.config import settings
from app.services.agrinet_routing import (
    AgrinetRouteDecision,
    get_alternate_agrinet_route,
    refresh_session_agrinet_route_ttl,
    resolve_agrinet_route,
    set_session_agrinet_route,
)
from app.services.chat_turn_map import write_chat_turn_map
from app.services.npss_response import post_process_npss_response
from app.tasks.telemetry import send_telemetry
from app.utils import (
    filter_thinking_from_history,
    format_message_pairs,
    trim_history,
    update_message_history,
)
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

logger = get_logger(__name__)

# EXPERIMENT: vLLM guided decoding (structured_outputs.regex) to stop the base
# model from drifting into other Indic/CJK/Korean scripts mid-answer. Allowed
# set = target script + full ASCII (covers tool-call JSON, English code-
# switching, markdown) + shared Indic punctuation/currency.
# Bengali and Assamese intentionally share one Unicode block (Assamese uses
# the Bengali script with two extra letters covered by the same range).
_GUIDED_DECODING_SCRIPT_RANGES = {
    "kn": "ಀ-೿",  # Kannada
    "ta": "஀-௿",  # Tamil
    "ml": "ഀ-ൿ",  # Malayalam
    "te": "ఀ-౿",  # Telugu
    "bn": "ঀ-৿",  # Bengali
    "as": "ঀ-৿",  # Assamese (Bengali script block)
    "gu": "઀-૿",  # Gujarati
}
_GUIDED_DECODING_SHARED_CHARS = (
    "\\t\\n\\r -~"  # ESCAPED tab/newline/CR (raw control bytes crash xgrammar's EBNF parser) + printable ASCII
    "।॥"            # shared Devanagari danda punctuation
    "–—‘’“”•₹"  # dashes/quotes/bullet/rupee
)


def _guided_decoding_settings(lang_code: str | None) -> OpenAIChatModelSettings | None:
    script_range = _GUIDED_DECODING_SCRIPT_RANGES.get((lang_code or "").lower())
    if not script_range:
        return None
    pattern = f"^[{script_range}{_GUIDED_DECODING_SHARED_CHARS}]*$"
    return OpenAIChatModelSettings(extra_body={"structured_outputs": {"regex": pattern}})

CHAT_TRACE_NAME = (
    os.getenv("LANGFUSE_TRACE_NAME")
    or os.getenv("LANGFUSE_TRACE_ROOT_NAME")
    or "bharat-vistaar-chat"
)
CHAT_CHAIN_SPAN_NAME = "chain.chat"


@dataclass
class _AgrinetCompletedRun:
    result: Any
    output_text: str


@dataclass
class _AgrinetStreamState:
    final_result_found: bool = False
    inside_think_block: bool = False
    raw_chunks: list[str] = field(default_factory=list)


def _agrinet_route_metadata(
    decision: AgrinetRouteDecision,
    *,
    fallback_used: bool,
    fallback_from: AgrinetRoute | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "agrinet_route": decision.route,
        "agrinet_model_name": decision.model_name,
        "route_source": decision.source,
        "fallback_used": fallback_used,
    }
    if fallback_from:
        metadata["fallback_from"] = fallback_from
    return metadata


def _strip_streaming_thinking_chunk(chunk: str, state: _AgrinetStreamState) -> str:
    if not chunk:
        return ""

    visible = chunk
    if state.inside_think_block:
        end_idx = visible.find("</think>")
        if end_idx < 0:
            return ""
        state.inside_think_block = False
        visible = visible[end_idx + len("</think>") :]

    if "<think>" in visible:
        end_idx = visible.find("</think>")
        if end_idx >= 0:
            visible = re.sub(r"<think>[\s\S]*?</think>", "", visible)
        else:
            state.inside_think_block = True
            visible = visible[: visible.find("<think>")]

    return visible


def _sanitize_streamed_output(raw_output: str) -> str:
    cleaned_output = re.sub(r"<think>[\s\S]*?</think>", "", raw_output)
    cleaned_output = re.sub(r"<think>[\s\S]*$", "", cleaned_output)
    return cleaned_output.strip()


def _queue_telemetry_event(background_tasks: BackgroundTasks, event: Any) -> None:
    background_tasks.add_task(
        send_telemetry,
        TelemetryRequest(events=create_frontend_compatible_item_batch(event)).model_dump(),
    )


async def _record_chat_turn(
    trace_id: str | None,
    telemetry_qid: str,
    session_id: str,
    decision: AgrinetRouteDecision,
    channel: str,
) -> None:
    """Persist the qid -> Langfuse trace mapping so user feedback can be scored later."""
    if not trace_id:
        logger.warning(
            "No active Langfuse trace id for qid %s; feedback score will be skipped",
            telemetry_qid,
        )
        return
    await write_chat_turn_map(
        telemetry_qid,
        trace_id=trace_id,
        session_id=session_id,
        model_name=decision.model_name,
        agrinet_route=decision.route,
        channel=channel,
    )


def _wrap_image_analysis_message(
    base_user_message: str,
    latitude: Optional[float],
    longitude: Optional[float],
) -> str:
    if latitude is not None and longitude is not None:
        location_instruction = (
            f"Browser coordinates are available for this image upload "
            f"(latitude={latitude}, longitude={longitude}). "
            "You MUST call `analyze_crop_image`. The backend supplies these coordinates; "
            "do not pass or calculate location IDs."
        )
    else:
        location_instruction = (
            "No browser coordinates were sent with this image upload. "
            "Do NOT call any geocoding tool or try to provide NPSS state, district, subdistrict, or village IDs. "
            "Call `analyze_crop_image`; the backend will omit unresolved location fields rather than inventing IDs."
        )
    return (
        f"[USER UPLOADED A CROP IMAGE]\n\n"
        f"{base_user_message}\n\n"
        f"INSTRUCTION: The user has uploaded a crop image for pest/disease identification. "
        f"Use the exact image URL already present in the user's message or recent conversation history when calling `analyze_crop_image`. "
        f"{location_instruction} "
        f"Do NOT call `search_pests_diseases` automatically. "
        f"Present the NPSS result as a clean, farmer-friendly structured card in the Selected Language using this format:\n"
        f"**Pest:** <pest name>\n"
        f"**Crop:** <crop name>\n"
        f"**Cause:** <pathogen class, e.g. fungi / bacteria / virus>\n\n"
        f"<short symptoms/identification summary translated into the Selected Language>\n\n"
        f"Skip any field that is empty, null, or not present in the tool result. "
        f"Do not copy the NPSS description verbatim. Summarize only what the tool returned in 2-4 simple sentences, and translate the explanation for the farmer. "
        f"Do not add a bold label for the description - just output the summary text as a paragraph after the labeled fields. "
        f"If the tool returns multiple findings, show only the most relevant one. "
        f"Do NOT add treatment advice, prevention advice, spray recommendations, or any follow-up question."
    )


def _build_user_message(
    deps: FarmerContext,
    last_response: str,
    *,
    is_image_analysis: bool,
    latitude: Optional[float],
    longitude: Optional[float],
) -> str:
    base_user_message = deps.get_user_message()
    if is_image_analysis:
        base_user_message = _wrap_image_analysis_message(base_user_message, latitude, longitude)
    return f"{last_response}{base_user_message}"


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
    _queue_telemetry_event(background_tasks, question_event)

    route_decision = await resolve_agrinet_route(session_id, has_history=bool(history))
    logger.info(
        "Resolved agrinet route %s (%s) for session %s via %s",
        route_decision.route,
        route_decision.model_name,
        session_id,
        route_decision.source,
    )
    route_metadata = _agrinet_route_metadata(route_decision, fallback_used=False)

    lf_env = settings.langfuse_tracing_environment
    trace_meta = chat_trace_metadata_strings(
        source_lang=source_lang,
        target_lang=target_lang,
        environment=lf_env,
        channel=channel,
        query=query,
        qid=telemetry_qid,
    )
    trace_meta.update(
        {
            "agrinet_route": route_decision.route,
            "agrinet_model_name": route_decision.model_name,
            "route_source": route_decision.source,
            "fallback_used": "false",
        }
    )
    trace_tags = list(
        dict.fromkeys(
            [
                f"env:{lf_env}",
                f"channel:{channel}",
                f"model:{route_decision.model_name}",
                f"moderation:{LANGFUSE_MODERATION_MODEL_NAME}",
            ]
        )
    )

    with propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        metadata=trace_meta,
        tags=trace_tags,
        trace_name=CHAT_TRACE_NAME,
    ):
        try:
            lf_update_current_span(input=query, metadata=route_metadata)

            trace_id = get_client().get_current_trace_id()
            await _record_chat_turn(trace_id, telemetry_qid, session_id, route_decision, channel)

            deps = FarmerContext(
                query=query,
                lang_code=target_lang,
                session_id=session_id,
                question_id=telemetry_qid,
                latitude=latitude,
                longitude=longitude,
            )

            message_pairs = "\n\n".join(format_message_pairs(history, 3))
            logger.info("Message pairs: %s", message_pairs)
            last_response = (
                f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
            )

            user_message = _build_user_message(
                deps,
                last_response,
                is_image_analysis=is_image_analysis,
                latitude=latitude,
                longitude=longitude,
            )

            moderation_data = await _run_moderation(user_message, session_id)
            logger.info("Moderation data: %s", moderation_data)
            deps.update_moderation_str(str(moderation_data))
            user_message = _build_user_message(
                deps,
                last_response,
                is_image_analysis=is_image_analysis,
                latitude=latitude,
                longitude=longitude,
            )

            trimmed_history = trim_history(history, max_tokens=64_000)
            logger.info("Trimmed history length: %s messages", len(trimmed_history))
            trimmed_history = filter_thinking_from_history(trimmed_history)

            chunk_queue: asyncio.Queue[str | None] = asyncio.Queue()
            with propagate_attributes(tags=[moderation_data.category]):
                agrinet_task = asyncio.create_task(
                    _run_agrinet_with_failover_streaming(
                        user_message=user_message,
                        trimmed_history=trimmed_history,
                        deps=deps,
                        session_id=session_id,
                        user_id=user_id,
                        moderation_category=moderation_data.category,
                        initial_decision=route_decision,
                        chunk_queue=chunk_queue,
                    )
                )
                while True:
                    chunk = await chunk_queue.get()
                    if chunk is None:
                        break
                    yield chunk

                completed_run, final_route_decision, fallback_used = await agrinet_task

            final_route_metadata = _agrinet_route_metadata(
                final_route_decision,
                fallback_used=fallback_used,
                fallback_from=route_decision.route if fallback_used else None,
            )

            if trace_id and fallback_used:
                await _record_chat_turn(
                    trace_id, telemetry_qid, session_id, final_route_decision, channel
                )

            result = completed_run.result
            output_text = post_process_npss_response(
                text=completed_run.output_text,
                target_lang=target_lang,
                npss_used=deps.npss_used,
            )
            new_messages = result.new_messages()
            logger.info(
                "Agent run complete for session %s via route %s (%s)",
                session_id,
                final_route_decision.route,
                final_route_decision.model_name,
            )

            lf_update_current_span(output=output_text, metadata=final_route_metadata)

            answer_event = create_chat_answer_event(
                current_user=telemetry_user,
                qid=telemetry_qid,
                question_text=query,
                answer_text=output_text,
                session_id=session_id,
            )
            _queue_telemetry_event(background_tasks, answer_event)

            clean_new_messages = filter_thinking_from_history(list(new_messages or []))
            clean_new_messages = _replace_last_text_output(clean_new_messages, output_text)
            messages = [*history, *clean_new_messages]
            logger.info(
                "Updating message history for session %s with %s messages",
                session_id,
                len(messages),
            )
            await update_message_history(session_id, messages)
            await refresh_session_agrinet_route_ttl(session_id)

            get_client().flush()
        except Exception as exc:
            error_event = create_chat_error_event(
                current_user=telemetry_user,
                qid=telemetry_qid,
                session_id=session_id,
                error_text=f"{type(exc).__name__}: {exc}",
                question_text=query,
            )
            _queue_telemetry_event(background_tasks, error_event)
            if "agrinet_task" in locals() and not agrinet_task.done():
                agrinet_task.cancel()
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


async def _run_agrinet_with_failover(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    moderation_category: str,
    initial_decision: AgrinetRouteDecision,
):
    try:
        result = await _run_agrinet_once(
            user_message=user_message,
            trimmed_history=trimmed_history,
            deps=deps,
            session_id=session_id,
            user_id=user_id,
            moderation_category=moderation_category,
            decision=initial_decision,
            fallback_used=False,
        )
        return result, initial_decision, False
    except Exception as primary_exc:
        if not settings.agrinet_routing_enabled:
            raise

        fallback_route = get_alternate_agrinet_route(initial_decision.route)
        fallback_decision = AgrinetRouteDecision(
            route=fallback_route,
            model_name=get_agrinet_route_model_name(fallback_route),
            source="failover",
        )
        logger.warning(
            "Agrinet route %s failed for session %s; retrying on %s (%s): %s",
            initial_decision.route,
            session_id,
            fallback_decision.route,
            fallback_decision.model_name,
            primary_exc,
        )
        result = await _run_agrinet_once(
            user_message=user_message,
            trimmed_history=trimmed_history,
            deps=deps,
            session_id=session_id,
            user_id=user_id,
            moderation_category=moderation_category,
            decision=fallback_decision,
            fallback_used=True,
            fallback_from=initial_decision.route,
        )
        await set_session_agrinet_route(session_id, fallback_decision.route)
        return result, fallback_decision, True


async def _run_agrinet_with_failover_streaming(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    moderation_category: str,
    initial_decision: AgrinetRouteDecision,
    chunk_queue: asyncio.Queue[str | None],
):
    try:
        try:
            completed_run = await _run_agrinet_once_streaming(
                user_message=user_message,
                trimmed_history=trimmed_history,
                deps=deps,
                session_id=session_id,
                user_id=user_id,
                moderation_category=moderation_category,
                decision=initial_decision,
                fallback_used=False,
                chunk_queue=chunk_queue,
            )
            return completed_run, initial_decision, False
        except Exception as primary_exc:
            if not settings.agrinet_routing_enabled:
                raise

            fallback_route = get_alternate_agrinet_route(initial_decision.route)
            fallback_decision = AgrinetRouteDecision(
                route=fallback_route,
                model_name=get_agrinet_route_model_name(fallback_route),
                source="failover",
            )
            logger.warning(
                "Agrinet route %s failed for session %s; retrying on %s (%s): %s",
                initial_decision.route,
                session_id,
                fallback_decision.route,
                fallback_decision.model_name,
                primary_exc,
            )
            completed_run = await _run_agrinet_once_streaming(
                user_message=user_message,
                trimmed_history=trimmed_history,
                deps=deps,
                session_id=session_id,
                user_id=user_id,
                moderation_category=moderation_category,
                decision=fallback_decision,
                fallback_used=True,
                fallback_from=initial_decision.route,
                chunk_queue=chunk_queue,
            )
            await set_session_agrinet_route(session_id, fallback_decision.route)
            return completed_run, fallback_decision, True
    finally:
        await chunk_queue.put(None)


@observe(name=AGENT_VISTAAR, as_type="agent")
async def _run_agrinet_once_streaming(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    moderation_category: str,
    decision: AgrinetRouteDecision,
    fallback_used: bool,
    chunk_queue: asyncio.Queue[str | None] | None = None,
    fallback_from: AgrinetRoute | None = None,
) -> _AgrinetCompletedRun:
    route_metadata = _agrinet_route_metadata(
        decision,
        fallback_used=fallback_used,
        fallback_from=fallback_from,
    )
    observation_metadata = {
        "session_id": session_id,
        "user_id": user_id,
        "moderation_category": moderation_category,
        **route_metadata,
    }

    lf_update_current_observation(
        input=user_message,
        model=decision.model_name,
        metadata=observation_metadata,
    )

    stream_state = _AgrinetStreamState()
    result = None

    try:
        async with asyncio.timeout(settings.agrinet_model_timeout_seconds):
            async for event in agrinet_agent.run_stream_events(
                user_prompt=user_message,
                message_history=trimmed_history,
                deps=deps,
                model=get_agrinet_route_model(decision.route),
                model_settings=_guided_decoding_settings(deps.lang_code),
            ):
                if isinstance(event, FinalResultEvent):
                    stream_state.final_result_found = True
                    continue

                if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    if not stream_state.final_result_found:
                        continue
                    raw_chunk = event.delta.content_delta or ""
                    stream_state.raw_chunks.append(raw_chunk)
                    visible_chunk = _strip_streaming_thinking_chunk(raw_chunk, stream_state)
                    if visible_chunk and chunk_queue is not None:
                        await chunk_queue.put(visible_chunk)
                    continue

                if getattr(event, "event_kind", "") == "function_tool_result":
                    stream_state.final_result_found = False
                    continue

                if isinstance(event, AgentRunResultEvent):
                    result = event.result
    except TimeoutError as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": "TimeoutError",
                "timeout_seconds": settings.agrinet_model_timeout_seconds,
            }
        )
        raise TimeoutError(
            f"Agrinet route {decision.route} timed out after {settings.agrinet_model_timeout_seconds} seconds"
        ) from exc
    except Exception as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": type(exc).__name__,
            }
        )
        raise

    if result is None:
        raise RuntimeError("Agrinet stream finished without a final result")

    fallback_output = str(getattr(result, "output", "") or "")
    streamed_output = _sanitize_streamed_output("".join(stream_state.raw_chunks))
    output_text = streamed_output or _sanitize_streamed_output(fallback_output)

    usage_data = result.usage()
    lf_update_current_observation(
        output=output_text,
        model=decision.model_name,
        input_tokens=usage_data.input_tokens or 0,
        output_tokens=usage_data.output_tokens or 0,
        metadata=observation_metadata,
    )
    return _AgrinetCompletedRun(result=result, output_text=output_text)


@observe(name=AGENT_VISTAAR, as_type="agent")
async def _run_agrinet_once(
    user_message: str,
    trimmed_history: list,
    deps: FarmerContext,
    session_id: str,
    user_id: str,
    moderation_category: str,
    decision: AgrinetRouteDecision,
    fallback_used: bool,
    fallback_from: AgrinetRoute | None = None,
):
    """Run the agrinet agent for a specific model route and trace it in Langfuse."""
    route_metadata = _agrinet_route_metadata(
        decision,
        fallback_used=fallback_used,
        fallback_from=fallback_from,
    )
    observation_metadata = {
        "session_id": session_id,
        "user_id": user_id,
        "moderation_category": moderation_category,
        **route_metadata,
    }

    lf_update_current_observation(
        input=user_message,
        model=decision.model_name,
        metadata=observation_metadata,
    )

    try:
        result = await asyncio.wait_for(
            agrinet_agent.run(
                user_prompt=user_message,
                message_history=trimmed_history,
                deps=deps,
                model=get_agrinet_route_model(decision.route),
                model_settings=_guided_decoding_settings(deps.lang_code),
            ),
            timeout=settings.agrinet_model_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": "TimeoutError",
                "timeout_seconds": settings.agrinet_model_timeout_seconds,
            }
        )
        raise TimeoutError(
            f"Agrinet route {decision.route} timed out after {settings.agrinet_model_timeout_seconds} seconds"
        ) from exc
    except Exception as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": type(exc).__name__,
            }
        )
        raise

    usage_data = result.usage()
    lf_update_current_observation(
        output=result.output,
        model=decision.model_name,
        input_tokens=usage_data.input_tokens or 0,
        output_tokens=usage_data.output_tokens or 0,
        metadata=observation_metadata,
    )
    return result
