"""Redis join map correlating a chat turn's qid with its Langfuse trace.

Written while the chat's Langfuse trace is still live (see app/services/chat.py)
and read later by the feedback endpoint (app/routers/telemetry.py) so user
feedback can be attached to that same finished Q&A trace instead of creating a
separate feedback trace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.utils import DEFAULT_CACHE_TTL, get_cache, set_cache

CHAT_TURN_KEY_PREFIX = "chat_turn"


def _chat_turn_key(qid: str) -> str:
    return f"{CHAT_TURN_KEY_PREFIX}:{qid}"


async def write_chat_turn_map(
    qid: str,
    *,
    trace_id: str,
    session_id: str,
    model_name: str,
    agrinet_route: str,
    channel: str,
    ttl: int = DEFAULT_CACHE_TTL,
) -> None:
    if not qid or not trace_id:
        return
    await set_cache(
        _chat_turn_key(qid),
        {
            "trace_id": trace_id,
            "session_id": session_id,
            "model_name": model_name,
            "agrinet_route": agrinet_route,
            "channel": channel,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ttl=ttl,
    )


async def read_chat_turn_map(qid: str) -> Optional[dict[str, Any]]:
    if not qid:
        return None
    return await get_cache(_chat_turn_key(qid))
