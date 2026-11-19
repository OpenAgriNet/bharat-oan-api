"""One voice turn: run the voice agent and stream Sarvam's {audio, end_interaction, language} JSON.

Routing and fallback use the shared model registry/router under the `voice` use case;
history and telemetry use the voice-only modules in this package.
"""
from typing import AsyncGenerator
import asyncio
import json
import re
from agents.voice import VOICE_AGENT, VOICE_USE_CASE, voice_agent
from agents.deps import FarmerContext
from agents.model_registry import get_registry
from agents.skill_policy import SkillState
from app.config import settings
from app.services.model_routing import get_model_router
from app.voice.languages import get_language, iso_language_code
from app.voice import telemetry as voice_telemetry
from app.voice.history import clean_voice_history, trim_voice_history, update_voice_history
from app.voice.observability import safe_propagate_attributes, safe_start_agent_observation, safe_update_observation
from helpers.telemetry.voice import create_voice_response_event, generate_voice_question_id
from helpers.utils import get_logger

logger = get_logger(__name__)

_FEEDBACK_ASK_RE = re.compile(
    r"feedback|फीडबैक|ফিডব্যাক|ફીડબેક|ಪ್ರತಿಕ್ರಿಯೆ|ഫീഡ്ബാക്ക്|फीडबॅक|ଫିଡ୍|ਫੀਡਬੈਕ|கருத்து|ఫీడ్‌బ్యాక్",
    re.IGNORECASE,
)


async def _run_events_with_timeout(**kwargs):
    deadline = asyncio.get_running_loop().time() + get_registry().get_timeout(VOICE_USE_CASE)
    events = voice_agent.run_stream_events(**kwargs)
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Voice model attempt timed out")
            try:
                yield await asyncio.wait_for(events.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
    finally:
        await events.aclose()


def _user_message_count(history: list) -> int:
    """Count user messages in history (messages that have a user-prompt part)."""
    if not history:
        return 0
    count = 0
    for msg in history:
        for part in msg.parts:
            if getattr(part, "part_kind", "") == "user-prompt":
                count += 1
                break
    return count


def _is_first_user_message(history: list) -> bool:
    """Check if this is the first user message after welcome messages."""
    return _user_message_count(history) == 1


def _last_assistant_text(history: list) -> str:
    """Return the latest assistant text turn from history."""
    if not history:
        return ""
    for msg in reversed(history):
        for part in msg.parts:
            if getattr(part, "part_kind", "") == "text":
                return str(getattr(part, "content", "") or "")
    return ""


def _feedback_prompt_seen(history: list) -> bool:
    """True only when the latest assistant turn clearly asked for feedback."""
    return bool(_FEEDBACK_ASK_RE.search(_last_assistant_text(history)))

def _get_recording_message(lang: str | None) -> str:
    """Get the recording disclaimer in the response language."""
    return get_language(lang).recording_disclaimer


def _turn_prefix(lang: str | None, is_first_message: bool) -> str:
    """Fixed text spoken ahead of the LLM's answer on the caller's first turn only:
    the hold message, then the recording disclaimer. Later turns get no prefix."""
    if not is_first_message:
        return ""
    parts = []
    if settings.voice_hold_message_enabled:
        parts.append(get_language(lang).hold_message)
    parts.append(_get_recording_message(lang))
    return " ".join(p for p in parts if p)


_DELAY_ELAPSED = object()


async def _events_with_deadline(events, deadline: float | None):
    """Pass agent events through, yielding _DELAY_ELAPSED once if the next event is
    still pending at `deadline` (event-loop time). The pending read is never
    cancelled while the turn is live: cancelling it would abort the agent run."""
    iterator = events.__aiter__()
    loop = asyncio.get_running_loop()
    while True:
        if deadline is None:
            try:
                event = await iterator.__anext__()
            except StopAsyncIteration:
                return
        else:
            pending = asyncio.ensure_future(iterator.__anext__())
            try:
                done, _ = await asyncio.wait({pending}, timeout=max(0.0, deadline - loop.time()))
                if not done:
                    deadline = None
                    yield _DELAY_ELAPSED
                event = await pending
            except StopAsyncIteration:
                return
            finally:
                if not pending.done():
                    pending.cancel()
        yield event


def _prefix_disclaimer(disclaimer: str, audio: str) -> str:
    if not disclaimer:
        return audio
    if not audio:
        return disclaimer
    return f"{disclaimer} {audio.lstrip()}"

def _voice_output_dict(audio: str, end_interaction: bool, language: str | None) -> dict:
    """Build the voice response with its public Sarvam language code."""
    return {"audio": audio, "end_interaction": end_interaction, "language": language}


async def stream_voice_message(
    query: str,
    session_id: str,
    user_id: str,
    history: list,
    language_code: str,
    *,
    emit_partial: bool = True,
    ffs_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Async generator for streaming voice messages using run_stream_events()."""
    history = clean_voice_history(history)
    response_language = iso_language_code(language_code)
    voice_qid = generate_voice_question_id()
    deps = FarmerContext(
        channel="voice",
        skills=SkillState(agent=VOICE_AGENT),
        query=query,
        session_id=session_id,
        question_id=voice_qid,
        user_id=user_id,
        lang_code=language_code,
        ffs_context=ffs_context or {},
    )
    user_message = deps.get_user_message()
    logger.info(
        "Running agent (language=%s, voice_qid=%s)", response_language, voice_qid
    )

    trimmed_history = trim_voice_history(history, max_tokens=80_000)
    logger.info(f"Trimmed history: {len(trimmed_history)} messages")
    deps.feedback_prompt_seen = _feedback_prompt_seen(trimmed_history)

    is_first_message = _is_first_user_message(history)
    turn_prefix = _turn_prefix(language_code, is_first_message)
    delay_deadline = (
        asyncio.get_running_loop().time() + settings.voice_delay_message_after_seconds
        if settings.voice_delay_message_enabled
        else None
    )

    # On the first turn, send the fixed prefix before the model runs so the caller hears
    # something while the LLM and any tools work. Every later chunk repeats it at the front, because
    # each chunk carries the whole reply so far.
    if settings.voice_hold_message_enabled and turn_prefix:
        yield json.dumps(
            _voice_output_dict(turn_prefix, False, response_language), ensure_ascii=False
        )
    registry = get_registry()
    router = get_model_router()
    decision = await router.resolve(
        VOICE_USE_CASE,
        session_id,
        has_history=_user_message_count(history) > 1,
    )
    model_route = decision.route
    model = registry.get_model(model_route)
    model_name = getattr(model, "model_name", "unknown")
    logger.info(
        "Routing session %s to model_route=%s model=%s source=%s",
        session_id,
        model_route,
        model_name,
        decision.source,
    )

    # Retry the alias-level fallback only before output reaches a streaming client.
    # Non-streaming calls suppress partial output, so they can safely retry through
    # the end of the model attempt.
    candidates = [(model, model_route)]
    fallback_alias = registry.get_fallback_alias(model_route)
    if fallback_alias:
        candidates.append((registry.get_model(fallback_alias), fallback_alias))

    final_output = None
    new_messages = None
    delay_sent = False

    agent_slug = (voice_agent.name or "voice").replace(" ", "_").lower()
    # Tags are trace-level in Langfuse, so they go through propagate_attributes;
    # observations only carry metadata.
    with safe_propagate_attributes(
        tags=["voice", "pydantic_ai", f"agent:{agent_slug}", f"model_route:{model_route}", f"model:{model_name}"],
    ), safe_start_agent_observation(
        name=f"agent.{agent_slug}",
        input={
            "query": query,
            "language": response_language,
            "session_id": session_id,
        },
        metadata={
            "agent_name": voice_agent.name,
            "voice_qid": voice_qid,
            "user_id": user_id,
            "model_route": model_route,
            "model_name": model_name,
        },
    ) as agent_obs:
        for attempt_index, (attempt_model, attempt_route) in enumerate(candidates):
            final_output = None
            new_messages = None
            text_buffer = ""
            prev_audio = ""
            any_chunk_yielded = False
            is_last_attempt = attempt_index == len(candidates) - 1
            try:
                async for event in _events_with_deadline(
                    _run_events_with_timeout(
                        user_prompt=user_message,
                        message_history=trimmed_history,
                        deps=deps,
                        model=attempt_model,
                    ),
                    None if delay_sent else delay_deadline,
                ):
                    if event is _DELAY_ELAPSED:
                        # No answer yet: apologise for the wait. Like the turn prefix, it
                        # stays at the front of every later chunk.
                        if not any_chunk_yielded:
                            delay_sent = True
                            turn_prefix = _prefix_disclaimer(
                                turn_prefix, get_language(language_code).delay_message
                            )
                            yield json.dumps(
                                _voice_output_dict(turn_prefix, False, response_language),
                                ensure_ascii=False,
                            )
                        continue

                    kind = getattr(event, 'event_kind', '')

                    if kind == 'part_delta':
                        delta = event.delta
                        if getattr(delta, 'part_delta_kind', '') == 'text':
                            text_buffer += delta.content_delta
                            audio = text_buffer.strip()
                            if emit_partial and audio and audio != prev_audio:
                                any_chunk_yielded = True
                                prev_audio = audio
                                output_dict = _voice_output_dict(
                                    _prefix_disclaimer(turn_prefix, audio),
                                    False,
                                    response_language,
                                )
                                yield json.dumps(output_dict, ensure_ascii=False)

                    elif kind == 'function_tool_result':
                        # Reset text buffer for next model turn
                        text_buffer = ""
                        prev_audio = ""

                    elif kind == 'agent_run_result':
                        agent_result = event.result
                        final_output = agent_result.output
                        new_messages = agent_result.new_messages()
                if final_output is None:
                    raise RuntimeError("Voice model returned no final output")
            except Exception as e:
                fallback_errors = registry.get_fallback_errors(attempt_route)
                if any_chunk_yielded or is_last_attempt or not isinstance(e, fallback_errors):
                    raise
                logger.warning(
                    f"model_route={attempt_route} call failed before streaming any output "
                    f"(session={session_id}): {e}. Retrying with fallback model."
                )
                continue

            if attempt_route != model_route:
                await router.store(VOICE_USE_CASE, session_id, attempt_route)
                model_route = attempt_route
                model_name = getattr(attempt_model, "model_name", "unknown")
                # Tag the active agent span so the trace is filterable by the route
                # actually served, not just the one initially selected.
                with safe_propagate_attributes(
                    tags=[f"model_route:{model_route}", f"model:{model_name}"],
                ):
                    logger.info(f"Session {session_id} recovered via runtime fallback to model_route={model_route}")
                try:
                    agent_obs.update(metadata={"model_route": model_route, "model_name": model_name})
                except (TypeError, AttributeError):
                    pass
            break

        if final_output is not None:
            safe_update_observation(
                agent_obs,
                {
                    "audio": (final_output or "")[:2000],
                    "end_interaction": deps.end_interaction,
                },
            )

    agent_response_text: str | None = final_output

    if final_output:
        end_flag = deps.end_interaction
        raw_audio = final_output

        audio_text = _prefix_disclaimer(turn_prefix, raw_audio)
        output_dict = _voice_output_dict(audio_text, end_flag, response_language)
        yield json.dumps(output_dict, ensure_ascii=False)
        logger.info(
            "Streaming complete - end_interaction: %s, language: %s",
            end_flag,
            response_language,
        )

    event = create_voice_response_event(
        uid=user_id or "guest",
        session_id=session_id,
        channel=settings.voice_telemetry_channel,
        question_text=query,
        qid=voice_qid,
        source_lang=response_language,
        target_lang=response_language,
        response_text=agent_response_text,
    )
    # Not tied to the request, so a caller hanging up does not cancel delivery.
    voice_telemetry.run_in_background(
        voice_telemetry.send_voice_event(event, label=f"OE_VOICE_RESPONSE qid={voice_qid}")
    )

    # Update message history
    if new_messages:
        await update_voice_history(session_id, [*history, *new_messages])
