"""Call history for voice sessions, stored apart from chat history.

Differences from chat (app/utils.py), kept from voice-oan-api:
- a new call starts with a scripted welcome exchange in the caller's language;
- trimming always keeps that welcome exchange;
- the injected FFS snapshot is stripped from the saved transcript;
- a shorter TTL (VOICE_HISTORY_TTL_SECONDS, default 1 hour).
"""
from copy import deepcopy
from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Dict, List

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python

from app.config import settings
from app.core.cache import cache
from app.utils import convo_token_usage
from app.voice import telemetry as voice_telemetry
from helpers.ffs_model_view import compact_ffs_result
from helpers.telemetry.voice import create_voice_response_event, generate_voice_question_id

logger = logging.getLogger(__name__)

_WELCOME_PATH = Path(__file__).resolve().parents[2] / "assets/voice/welcome_messages.json"
# Distinct from chat's "{session}__SVA" so a voice and a chat session can never share history.
_HISTORY_KEY = "{session_id}_VOICE_HISTORY"


def history_key(session_id: str) -> str:
    return _HISTORY_KEY.format(session_id=session_id)


@lru_cache(maxsize=1)
def load_welcome_messages() -> Dict[str, Dict[str, str]]:
    with open(_WELCOME_PATH, encoding="utf-8") as f:
        return json.load(f)


def _welcome_pair(user_message: str, assistant_message: str) -> List[ModelMessage]:
    """The scripted opening; the system prompt is applied by the agent, not stored here."""
    return [
        ModelRequest(parts=[UserPromptPart(content=user_message)]),
        ModelResponse(parts=[TextPart(content=assistant_message)]),
    ]


async def get_voice_history(
    session_id: str,
    target_lang: str = "hi",
    user_id: str | None = None,
) -> List[ModelMessage]:
    """Load a call's history, or start it with the welcome exchange."""
    stored = await cache.get(history_key(session_id))
    if stored:
        return clean_voice_history(ModelMessagesTypeAdapter.validate_python(stored))

    welcome_messages = load_welcome_messages()
    welcome = welcome_messages.get(target_lang, welcome_messages["hi"])
    pair = _welcome_pair(welcome["user"], welcome["assistant"])
    await cache.set(history_key(session_id), to_jsonable_python(pair), ttl=settings.voice_history_ttl_seconds)

    # Welcome turn: same OE_VOICE_RESPONSE shape, without questionText (the user line is scripted).
    event = create_voice_response_event(
        uid=user_id or "guest",
        session_id=session_id,
        channel=settings.voice_telemetry_channel,
        qid=generate_voice_question_id(),
        source_lang=target_lang,
        target_lang=target_lang,
        response_text=welcome["assistant"],
    )
    voice_telemetry.run_in_background(voice_telemetry.send_voice_event(event, label="welcome OE_VOICE_RESPONSE"))
    return pair


async def update_voice_history(session_id: str, all_messages: List[ModelMessage]) -> None:
    await cache.set(
        history_key(session_id),
        to_jsonable_python(clean_voice_history(all_messages)),
        ttl=settings.voice_history_ttl_seconds,
    )


def _strip_saved_voice_context(text: str) -> str:
    """Strip our appended snapshot, preserving the quoted transcript verbatim."""
    before, marker, _snapshot = text.rpartition("\n**FFS workflow:** ")
    if marker and not text.endswith('"') and before.startswith('**User:** "') and before.endswith('"'):
        return before
    return text


def clean_voice_history(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Keep conversation/tool evidence, without saved copies of injected context.

    Apply on load for existing sessions and on save for new turns. Only user-prompt
    strings change; tool parts, images and assistant output remain intact.
    """
    cleaned = []
    for message in messages:
        copy = message
        for index, part in enumerate(message.parts):
            if getattr(part, "part_kind", "") == "tool-return" and getattr(part, "tool_name", "") in {"ffs_find_shops", "ffs_create_booking"}:
                compact = compact_ffs_result(part.content)
                if compact != part.content:
                    if copy is message:
                        copy = deepcopy(message)
                    copy.parts[index].content = compact
                continue
            if getattr(part, "part_kind", "") != "user-prompt":
                continue
            content = part.content
            if isinstance(content, str):
                stripped = _strip_saved_voice_context(content)
            elif isinstance(content, list):
                stripped = [_strip_saved_voice_context(item) if isinstance(item, str) else item for item in content]
            else:
                continue
            if stripped != content:
                if copy is message:
                    copy = deepcopy(message)
                copy.parts[index].content = stripped
        cleaned.append(copy)
    return cleaned


def group_voice_convos(history: List[ModelMessage]) -> List[List[ModelMessage]]:
    """Group messages into turns; the first group is the welcome exchange."""
    if not history:
        return []
    user_indices = [
        i for i, msg in enumerate(history)
        if any(getattr(part, "part_kind", "") == "user-prompt" for part in msg.parts)
    ]
    if len(user_indices) < 2:
        return [history]
    groups = [history[:user_indices[1]]]
    for idx in range(1, len(user_indices)):
        start = user_indices[idx]
        end = user_indices[idx + 1] if idx + 1 < len(user_indices) else len(history)
        groups.append(history[start:end])
    return groups


def trim_voice_history(history: List[ModelMessage], max_tokens: int = 80_000) -> List[ModelMessage]:
    """Keep the welcome exchange plus as many recent turns as fit in max_tokens."""
    convos = group_voice_convos(history)
    if not convos:
        return []
    first, rest = convos[0], convos[1:]
    total = convo_token_usage(first)
    selected: List[List[ModelMessage]] = []
    for convo in reversed(rest):
        tokens = convo_token_usage(convo)
        if total + tokens > max_tokens:
            break
        selected.insert(0, convo)
        total += tokens
    logger.info("Trimmed voice history: ~%s tokens (max: %s)", total, max_tokens)
    return [message for convo in [first, *selected] for message in convo]
