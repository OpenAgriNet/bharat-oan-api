import asyncio
import os
import re
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, AsyncGenerator, Optional

from fastapi import BackgroundTasks
from langfuse import get_client, observe, propagate_attributes
from pydantic_ai import AgentRunResultEvent, FinalResultEvent, PartDeltaEvent, PartStartEvent, TextPartDelta
from pydantic_ai.messages import TextPart
from pydantic_ai.models.openai import OpenAIChatModelSettings

from agents.agrinet import agrinet_agent
from agents.deps import FarmerContext
from agents.model_registry import get_registry
from agents.models import (
    LANGFUSE_MODERATION_MODEL_NAME,
    AgrinetRoute,
    get_agrinet_route_model,
    get_agrinet_route_model_name,
)
from agents.moderation import moderation_agent
from app.config import settings
from app.core.npss_followup import find_pending_npss_image_url
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

# vLLM guided decoding (structured_outputs.regex) to stop the base model from
# drifting into another script mid-answer. This is an ALLOWLIST (own script +
# ASCII + Unicode Common/Inherited) — NOT a denylist of other scripts. A
# denylist over non-ASCII ranges silently fails to enforce on xgrammar
# (confirmed bug: https://github.com/mlc-ai/xgrammar/issues/848 — negated
# classes containing bytes >127 get clamped to 127 and the exclusion never
# actually applies, which also explains the degenerate-repetition behavior
# seen when testing the denylist form).
# Common/Inherited (verified via the `regex` module's Script property, not
# guessed) covers every shared punctuation/symbol/emoji/combining-mark
# automatically — e.g. danda (U+0964/U+0965) and ZWJ/ZWNJ (U+200C/200D) are
# both Common/Inherited despite looking script-specific, which is exactly
# what #142/#167 got wrong by hand-picking a punctuation allowlist instead.
_SCRIPT_EXCLUSIVE_RANGES = {
    "bn": "\u0980-\u0983\u0985-\u098c\u098f-\u0990\u0993-\u09a8\u09aa-\u09b0\u09b2\u09b6-\u09b9\u09bc-\u09c4\u09c7-\u09c8\u09cb-\u09ce\u09d7\u09dc-\u09dd\u09df-\u09e3\u09e6-\u09fe",  # Bengali (Assamese shares this block)
    "gu": "\u0a81-\u0a83\u0a85-\u0a8d\u0a8f-\u0a91\u0a93-\u0aa8\u0aaa-\u0ab0\u0ab2-\u0ab3\u0ab5-\u0ab9\u0abc-\u0ac5\u0ac7-\u0ac9\u0acb-\u0acd\u0ad0\u0ae0-\u0ae3\u0ae6-\u0af1\u0af9-\u0aff",  # Gujarati
    "kn": "\u0c80-\u0c8c\u0c8e-\u0c90\u0c92-\u0ca8\u0caa-\u0cb3\u0cb5-\u0cb9\u0cbc-\u0cc4\u0cc6-\u0cc8\u0cca-\u0ccd\u0cd5-\u0cd6\u0cdc-\u0cde\u0ce0-\u0ce3\u0ce6-\u0cef\u0cf1-\u0cf3",  # Kannada
    "ta": "\u0b82-\u0b83\u0b85-\u0b8a\u0b8e-\u0b90\u0b92-\u0b95\u0b99-\u0b9a\u0b9c\u0b9e-\u0b9f\u0ba3-\u0ba4\u0ba8-\u0baa\u0bae-\u0bb9\u0bbe-\u0bc2\u0bc6-\u0bc8\u0bca-\u0bcd\u0bd0\u0bd7\u0be6-\u0bfa",  # Tamil
    "te": "\u0c00-\u0c0c\u0c0e-\u0c10\u0c12-\u0c28\u0c2a-\u0c39\u0c3c-\u0c44\u0c46-\u0c48\u0c4a-\u0c4d\u0c55-\u0c56\u0c58-\u0c5a\u0c5c-\u0c5d\u0c60-\u0c63\u0c66-\u0c6f\u0c77-\u0c7f",  # Telugu
    "ml": "\u0d00-\u0d0c\u0d0e-\u0d10\u0d12-\u0d44\u0d46-\u0d48\u0d4a-\u0d4f\u0d54-\u0d63\u0d66-\u0d7f",  # Malayalam
}
_ASCII_RANGE = "\t\n\r \u0020-\u007e"  # printable ASCII + whitespace; raw control bytes (NUL etc.) break xgrammar's parser
_COMMON_INHERITED_RANGE = (  # 181 ranges, computed from the full Unicode codepoint space (excludes Cc control chars)
    "\u0020-\u0040\u005b-\u0060\u007b-\u007e\u00a0-\u00a9\u00ab-\u00b9\u00bb-\u00bf\u00d7\u00f7\u02b9-\u02df\u02e5-\u02e9\u02ec-\u036f\u0374\u037e\u0385\u0387\u0485-\u0486\u0605\u060c\u061b\u061f\u0640\u064b-\u0655\u0670\u06dd\u08e2\u0951-\u0954\u0964-\u0965\u0e3f\u0fd5-\u0fd8\u10fb\u16eb-\u16ed\u1735-\u1736\u1802-\u1803\u1805\u1ab0-\u1add\u1ae0-\u1aeb\u1cd0-\u1cfa\u1dc0-\u1dff\u2000-\u2064\u2066-\u2070\u2074-\u207e\u2080-\u208e\u20a0-\u20c1\u20d0-\u20f0\u2100-\u2125\u2127-\u2129\u212c-\u2131\u2133-\u214d\u214f-\u215f\u2189-\u218b\u2190-\u2429\u2440-\u244a\u2460-\u27ff\u2900-\u2b73\u2b76-\u2bff\u2e00-\u2e5d\u2ff0-\u3004\u3006\u3008-\u3020\u302a-\u302d\u3030-\u3037\u303c-\u303f\u3099-\u309c\u30a0\u30fb-\u30fc\u3190-\u319f\u31c0-\u31e5\u31ef\u3220-\u325f\u327f-\u32cf\u32ff\u3358-\u33ff\u4dc0-\u4dff\ua700-\ua721\ua788-\ua78a\ua830-\ua839\ua92e\ua9cf\uab5b\uab6a-\uab6b\ufd3e-\ufd3f\ufe00-\ufe19\ufe20-\ufe2d\ufe30-\ufe52\ufe54-\ufe66\ufe68-\ufe6b\ufeff\uff01-\uff20\uff3b-\uff40\uff5b-\uff65\uff70\uff9e-\uff9f\uffe0-\uffe6\uffe8-\uffee\ufff9-\ufffd\U00010100-\U00010102\U00010107-\U00010133\U00010137-\U0001013f\U00010190-\U0001019c\U000101d0-\U000101fd\U000102e0-\U000102fb\U0001133b\U0001bca0-\U0001bca3\U0001cc00-\U0001ccfc\U0001cd00-\U0001ceb3\U0001ceba-\U0001ced0\U0001cee0-\U0001cef0\U0001cf00-\U0001cf2d\U0001cf30-\U0001cf46\U0001cf50-\U0001cfc3\U0001d000-\U0001d0f5\U0001d100-\U0001d126\U0001d129-\U0001d1ea\U0001d2c0-\U0001d2d3\U0001d2e0-\U0001d2f3\U0001d300-\U0001d356\U0001d360-\U0001d378\U0001d400-\U0001d454\U0001d456-\U0001d49c\U0001d49e-\U0001d49f\U0001d4a2\U0001d4a5-\U0001d4a6\U0001d4a9-\U0001d4ac\U0001d4ae-\U0001d4b9\U0001d4bb\U0001d4bd-\U0001d4c3\U0001d4c5-\U0001d505\U0001d507-\U0001d50a\U0001d50d-\U0001d514\U0001d516-\U0001d51c\U0001d51e-\U0001d539\U0001d53b-\U0001d53e\U0001d540-\U0001d544\U0001d546\U0001d54a-\U0001d550\U0001d552-\U0001d6a5\U0001d6a8-\U0001d7cb\U0001d7ce-\U0001d7ff\U0001ec71-\U0001ecb4\U0001ed01-\U0001ed3d\U0001f000-\U0001f02b\U0001f030-\U0001f093\U0001f0a0-\U0001f0ae\U0001f0b1-\U0001f0bf\U0001f0c1-\U0001f0cf\U0001f0d1-\U0001f0f5\U0001f100-\U0001f1ad\U0001f1e6-\U0001f1ff\U0001f201-\U0001f202\U0001f210-\U0001f23b\U0001f240-\U0001f248\U0001f250-\U0001f251\U0001f260-\U0001f265\U0001f300-\U0001f6d8\U0001f6dc-\U0001f6ec\U0001f6f0-\U0001f6fc\U0001f700-\U0001f7d9\U0001f7e0-\U0001f7eb\U0001f7f0\U0001f800-\U0001f80b\U0001f810-\U0001f847\U0001f850-\U0001f859\U0001f860-\U0001f887\U0001f890-\U0001f8ad\U0001f8b0-\U0001f8bb\U0001f8c0-\U0001f8c1\U0001f8d0-\U0001f8d8\U0001f900-\U0001fa57\U0001fa60-\U0001fa6d\U0001fa70-\U0001fa7c\U0001fa80-\U0001fa8a\U0001fa8e-\U0001fac6\U0001fac8\U0001facd-\U0001fadc\U0001fadf-\U0001faea\U0001faef-\U0001faf8\U0001fb00-\U0001fb92\U0001fb94-\U0001fbfa\U000e0001\U000e0020-\U000e007f\U000e0100-\U000e01ef"
)


def _escape_for_char_class(chars: str) -> str:
    """Escape metacharacters and control bytes before handing a character set
    to xgrammar: a raw '[' mid-class is misparsed as a nested bracket, and raw
    control bytes (tab/newline/CR) break its EBNF lexer — both need to arrive
    as regex escape sequences, not literal bytes."""
    chars = chars.replace("\\", "\\\\")
    chars = chars.replace("]", "\\]").replace("[", "\\[").replace("^", "\\^")
    return chars.replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


# Only these languages get the guided-decoding gate; Hindi/English aren't
# observed to mix scripts (per eval history), so no gate needed for them.
_GUIDED_DECODING_TARGETS = {"kn", "ta", "ml", "te", "bn", "as", "gu"}

# Precomputed once at import time, not per-request — compiling one of these
# patterns costs ~10ms cold and ~0.1ms on a cache hit (measured directly
# against xgrammar), so this is purely about avoiding repeated string-building
# work, not a real latency concern either way.
_GUIDED_DECODING_PATTERNS = {
    lang: f"^[{_escape_for_char_class(exclusive + _ASCII_RANGE + _COMMON_INHERITED_RANGE)}]*$"
    for lang, exclusive in _SCRIPT_EXCLUSIVE_RANGES.items()
}
_GUIDED_DECODING_PATTERNS["as"] = _GUIDED_DECODING_PATTERNS["bn"]  # Assamese shares Bengali's block


# `structured_outputs.regex` is a vLLM/xgrammar extra_body field — sending it
# to Azure/plain-OpenAI would either 400 or silently no-op. agrinet routes
# 50/50 between gemma_vllm and azure_gpt41 (config/models.yaml), so this must
# check the actual selected route's kind, not just assume vLLM.
# Deliberately vllm-only, not bharat_ai_grid: both share the same OpenAI-
# compatible client builder in model_registry.py, but that only means they
# speak the same wire protocol — it says nothing about whether Bharat AI
# Grid's backend actually runs vLLM/xgrammar. Untested, and that alias isn't
# even wired into any use case's routing yet, so don't assume.
_GUIDED_DECODING_CAPABLE_KINDS = {"vllm"}


def _guided_decoding_settings(lang_code: str | None, route: str) -> OpenAIChatModelSettings | None:
    lang_code = (lang_code or "").lower()
    if lang_code not in _GUIDED_DECODING_TARGETS:
        return None
    if get_registry().get_kind(route) not in _GUIDED_DECODING_CAPABLE_KINDS:
        return None
    return OpenAIChatModelSettings(
        extra_body={"structured_outputs": {"regex": _GUIDED_DECODING_PATTERNS[lang_code]}}
    )


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
    pending_text_start: str = ""
    raw_chunks: list[str] = field(default_factory=list)


class _StreamChunkSink:
    """Queue wrapper that remembers whether anything reached the client.

    A failover retry writes into the same queue the caller is already draining,
    so it is only safe before the first chunk goes out. Past that point the
    farmer has seen part of an answer and a retry would append a second one.
    """

    def __init__(self, queue: asyncio.Queue[str | None]) -> None:
        self._queue = queue
        self.emitted = False

    async def put(self, chunk: str) -> None:
        self.emitted = True
        await self._queue.put(chunk)


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


async def _emit_final_text_chunk(
    raw_chunk: str,
    stream_state: _AgrinetStreamState,
    chunk_queue: _StreamChunkSink | None,
) -> None:
    """Record and stream one final-answer text piece (PartStart prefix or delta)."""
    if not raw_chunk:
        return
    stream_state.raw_chunks.append(raw_chunk)
    visible_chunk = _strip_streaming_thinking_chunk(raw_chunk, stream_state)
    if visible_chunk and chunk_queue is not None:
        await chunk_queue.put(visible_chunk)


def _sanitize_streamed_output(raw_output: str) -> str:
    cleaned_output = re.sub(r"<think>[\s\S]*?</think>", "", raw_output)
    cleaned_output = re.sub(r"<think>[\s\S]*$", "", cleaned_output)
    return cleaned_output.strip()


def _queue_telemetry_event(background_tasks: BackgroundTasks, event: Any) -> None:
    background_tasks.add_task(
        send_telemetry,
        TelemetryRequest(events=create_frontend_compatible_item_batch(event)).model_dump(),
    )


async def _send_telemetry_event(event: Any) -> None:
    """Send an event inline: response background tasks never run once the client is gone."""
    try:
        await send_telemetry(
            TelemetryRequest(events=create_frontend_compatible_item_batch(event)).model_dump()
        )
    except Exception:
        logger.exception("Failed to send telemetry event after client disconnect")


# How long an in-flight agent run may keep going after the client hangs up, so the turn
# still reaches history and telemetry instead of disappearing mid-stream.
_DISCONNECT_FINALIZE_TIMEOUT_SECONDS = 180

# asyncio only holds weak references to running tasks, so detached finalizers need a
# strong reference here or they can be garbage collected mid-flight.
_detached_finalizers: set[asyncio.Task] = set()


def _spawn_detached(coro) -> None:
    """Run a coroutine outside the request's cancel scope so a disconnect can't kill it."""
    task = asyncio.create_task(coro)
    _detached_finalizers.add(task)
    task.add_done_callback(_detached_finalizers.discard)


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
    pending_npss_image_url: Optional[str] = None,
) -> str:
    if pending_npss_image_url:
        location_instruction = (
            "A previous NPSS call saved this image while waiting for location data. "
            f"The pending image URL is {pending_npss_image_url}. "
            "Call `analyze_crop_image` again with that image URL and no location. Do not ask the "
            "farmer for location details; the backend will use browser coordinates when present "
            "or immediately fall back to Krishi Vigyan Kendra, Delhi."
        )
    elif latitude is not None and longitude is not None:
        location_instruction = (
            f"Browser coordinates are available for this image upload "
            f"(latitude={latitude}, longitude={longitude}). "
            "You MUST call `analyze_crop_image`. The backend supplies these coordinates; "
            "do not pass or calculate location IDs."
        )
    else:
        location_instruction = (
            "No browser coordinates were sent with this image upload. "
            "Call `analyze_crop_image` immediately without a location. Do not ask the farmer for "
            "location details; the backend will immediately use the Krishi Vigyan Kendra, Delhi fallback."
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
        f"Never ask the farmer for location details. "
        f"Do NOT add treatment advice, prevention advice, spray recommendations, or any follow-up question."
    )


def _build_user_message(
    deps: FarmerContext,
    last_response: str,
    *,
    is_image_analysis: bool,
    latitude: Optional[float],
    longitude: Optional[float],
    pending_npss_image_url: Optional[str] = None,
) -> str:
    base_user_message = deps.get_user_message()
    if is_image_analysis or pending_npss_image_url:
        base_user_message = _wrap_image_analysis_message(
            base_user_message,
            latitude,
            longitude,
            pending_npss_image_url,
        )
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
            pending_npss_image_url = find_pending_npss_image_url(history)
            last_response = (
                f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
            )

            user_message = _build_user_message(
                deps,
                last_response,
                is_image_analysis=is_image_analysis,
                latitude=latitude,
                longitude=longitude,
                pending_npss_image_url=pending_npss_image_url,
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
                pending_npss_image_url=pending_npss_image_url,
            )

            trimmed_history = trim_history(history, max_tokens=64_000)
            logger.info("Trimmed history length: %s messages", len(trimmed_history))
            trimmed_history = filter_thinking_from_history(trimmed_history)

            chunk_queue: asyncio.Queue[str | None] = asyncio.Queue()
            defer_npss_output = bool(is_image_analysis or pending_npss_image_url)

            def _resolve_output_text(completed_run: _AgrinetCompletedRun) -> str:
                return post_process_npss_response(
                    text=completed_run.output_text,
                    target_lang=target_lang,
                    npss_used=deps.npss_used,
                )

            async def _finalize_turn(
                completed_run: _AgrinetCompletedRun,
                final_route_decision: AgrinetRouteDecision,
                fallback_used: bool,
                output_text: str,
                *,
                detached: bool = False,
            ) -> None:
                """Record the finished turn. Also runs detached after a client disconnect."""
                final_route_metadata = _agrinet_route_metadata(
                    final_route_decision,
                    fallback_used=fallback_used,
                    fallback_from=route_decision.route if fallback_used else None,
                )

                if trace_id and fallback_used:
                    await _record_chat_turn(
                        trace_id, telemetry_qid, session_id, final_route_decision, channel
                    )

                logger.info(
                    "Agent run complete for session %s via route %s (%s)",
                    session_id,
                    final_route_decision.route,
                    final_route_decision.model_name,
                )

                if detached:
                    # The span closed when the request task unwound, so there is nothing
                    # left to attach the output to.
                    logger.info("Finalizing session %s turn after client disconnect", session_id)
                else:
                    lf_update_current_span(output=output_text, metadata=final_route_metadata)

                answer_event = create_chat_answer_event(
                    current_user=telemetry_user,
                    qid=telemetry_qid,
                    question_text=query,
                    answer_text=output_text,
                    session_id=session_id,
                )
                if detached:
                    await _send_telemetry_event(answer_event)
                else:
                    _queue_telemetry_event(background_tasks, answer_event)

                new_messages = completed_run.result.new_messages()
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

            async def _finalize_after_disconnect(pending_run: asyncio.Task) -> None:
                """Let the in-flight run finish after a disconnect so the turn is still saved."""
                try:
                    completed_run, final_route_decision, fallback_used = await asyncio.wait_for(
                        pending_run, _DISCONNECT_FINALIZE_TIMEOUT_SECONDS
                    )
                    await _finalize_turn(
                        completed_run,
                        final_route_decision,
                        fallback_used,
                        _resolve_output_text(completed_run),
                        detached=True,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Agent run for session %s did not finish within %ss of the client "
                        "disconnecting; dropping the turn",
                        session_id,
                        _DISCONNECT_FINALIZE_TIMEOUT_SECONDS,
                    )
                except Exception:
                    logger.exception(
                        "Failed to save session %s turn after client disconnect", session_id
                    )

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
                try:
                    while True:
                        chunk = await chunk_queue.get()
                        if chunk is None:
                            break
                        if not defer_npss_output:
                            yield chunk

                    # Shielded so that cancelling this request does not cancel the run
                    # itself: the disconnect finalizer still needs its result.
                    completed_run, final_route_decision, fallback_used = await asyncio.shield(
                        agrinet_task
                    )
                except (asyncio.CancelledError, GeneratorExit):
                    # Client hung up mid-stream. Hand the still-running agent task to a
                    # detached finalizer, then let the cancellation continue.
                    _spawn_detached(_finalize_after_disconnect(agrinet_task))
                    raise

            output_text = _resolve_output_text(completed_run)
            if defer_npss_output:
                try:
                    yield output_text
                except (asyncio.CancelledError, GeneratorExit):
                    _spawn_detached(
                        _finalize_turn(
                            completed_run,
                            final_route_decision,
                            fallback_used,
                            output_text,
                            detached=True,
                        )
                    )
                    raise

            await _finalize_turn(
                completed_run, final_route_decision, fallback_used, output_text
            )
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
    sink = _StreamChunkSink(chunk_queue)
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
                chunk_queue=sink,
            )
            return completed_run, initial_decision, False
        except Exception as primary_exc:
            if not settings.agrinet_routing_enabled:
                raise
            if sink.emitted:
                # Part of the answer is already on its way to the farmer; a retry
                # would stream a second answer on top of it.
                logger.warning(
                    "Agrinet route %s failed for session %s after streaming began; "
                    "not retrying on the alternate route: %s",
                    initial_decision.route,
                    session_id,
                    primary_exc,
                )
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
                chunk_queue=sink,
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
    chunk_queue: _StreamChunkSink | None = None,
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
        async with asyncio.timeout(get_registry().get_timeout("agrinet")):
            async for event in agrinet_agent.run_stream_events(
                user_prompt=user_message,
                message_history=trimmed_history,
                deps=deps,
                model=get_agrinet_route_model(decision.route),
                model_settings=_guided_decoding_settings(deps.lang_code, decision.route),
            ):
                # Event order from pydantic-ai for the final answer:
                #   PartStartEvent(TextPart content="Hello")  # first tokens live here
                #   FinalResultEvent
                #   PartDeltaEvent(...content_delta=", I'm here...")
                # Deltas-only handling drops the PartStart prefix → ", I'm here..." in UI/Langfuse.
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    prefix = event.part.content or ""
                    if not prefix:
                        continue
                    if stream_state.final_result_found:
                        await _emit_final_text_chunk(prefix, stream_state, chunk_queue)
                    else:
                        stream_state.pending_text_start = prefix
                    continue

                if isinstance(event, FinalResultEvent):
                    stream_state.final_result_found = True
                    if stream_state.pending_text_start:
                        pending = stream_state.pending_text_start
                        stream_state.pending_text_start = ""
                        await _emit_final_text_chunk(pending, stream_state, chunk_queue)
                    continue

                if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    if not stream_state.final_result_found:
                        continue
                    await _emit_final_text_chunk(
                        event.delta.content_delta or "",
                        stream_state,
                        chunk_queue,
                    )
                    continue

                if getattr(event, "event_kind", "") == "function_tool_result":
                    stream_state.final_result_found = False
                    stream_state.pending_text_start = ""
                    continue

                if isinstance(event, AgentRunResultEvent):
                    result = event.result
    except TimeoutError as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": "TimeoutError",
                "timeout_seconds": get_registry().get_timeout("agrinet"),
            }
        )
        raise TimeoutError(
            f"Agrinet route {decision.route} timed out after {get_registry().get_timeout('agrinet')} seconds"
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

    # result.output is the fully assembled ModelResponse text (includes PartStart).
    # Prefer it so history/Langfuse stay complete even if a stream edge case is missed.
    full_output = _sanitize_streamed_output(str(getattr(result, "output", "") or ""))
    streamed_output = _sanitize_streamed_output("".join(stream_state.raw_chunks))
    output_text = full_output or streamed_output

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
                model_settings=_guided_decoding_settings(deps.lang_code, decision.route),
            ),
            timeout=get_registry().get_timeout("agrinet"),
        )
    except asyncio.TimeoutError as exc:
        lf_update_current_observation(
            metadata={
                **observation_metadata,
                "error_type": "TimeoutError",
                "timeout_seconds": get_registry().get_timeout("agrinet"),
            }
        )
        raise TimeoutError(
            f"Agrinet route {decision.route} timed out after {get_registry().get_timeout('agrinet')} seconds"
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