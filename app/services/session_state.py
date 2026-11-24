"""Per-session workflow state written by tools, read back once per chat turn.

Each workflow owns one section (e.g. "pmkisan", "ffs", "place"). A tool records what a later turn needs
(identifiers the farmer gave, the workflow stage, a short result summary) with `remember`, as part of the
tool call: no extra model call, and one Redis write per changed section at the end of the turn. The chat turn loads every section once (`load_state`) and the
agent sees them as one "**Session state:**" line in the user message, so old tool results need not be resent.

Keys use a hash of the session ID; values may hold personal data, so never log them.
"""
import hashlib
import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

NAMESPACE_PREFIX = "session-state"
DEFAULT_TTL_SECONDS = 24 * 3600
SESSION_STATE_LABEL = "**Session state (saved by tools in earlier turns):**"

# section -> ttl seconds. Sections must be registered so load_state can read them in one multi_get.
_SECTIONS: dict[str, int] = {}
# section -> function(section_state) -> what the model sees (dict/str) or None to hide the section.
_VIEWS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_section(name: str, ttl: int = DEFAULT_TTL_SECONDS,
                     view: Optional[Callable[[dict[str, Any]], Any]] = None) -> str:
    _SECTIONS[name] = ttl
    if view:
        _VIEWS[name] = view
    return name


def session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def namespace(section: str) -> str:
    return f"{NAMESPACE_PREFIX}:{section}"


def _deps_of(ctx_or_deps: Any) -> Any:
    return getattr(ctx_or_deps, "deps", ctx_or_deps)


async def load_state(session_id: str) -> dict[str, dict[str, Any]]:
    """Every registered section that has data: one MGET per turn (one round trip, one pooled connection).

    Parallel per-section GETs took one connection each and exhausted the Redis pool under concurrent chats.
    The keys equal those written with namespace=namespace(section): the key builder joins "<namespace>:<key>".
    """
    names = list(_SECTIONS)
    if not names or not session_id:
        return {}
    # Prompt rendering only uses in-memory state; load Redis dependencies when doing I/O.
    from app.core.cache import cache

    key = session_key(session_id)
    try:
        values = await cache.multi_get([f"{namespace(n)}:{key}" for n in names])
    except Exception as exc:
        logger.warning("session_state: read failed: %s", exc)
        return {}
    return {n: v for n, v in zip(names, values) if v}


async def get_section(session_id: str, section: str) -> dict[str, Any]:
    from app.core.cache import cache

    try:
        return await cache.get(session_key(session_id), namespace=namespace(section)) or {}
    except Exception as exc:
        logger.warning("session_state: read of %s failed: %s", section, exc)
        return {}


async def set_section(session_id: str, section: str, value: dict[str, Any]) -> None:
    """Replace a whole section (for workflows such as FFS that keep their own full state)."""
    from app.core.cache import cache

    try:
        if value:
            await cache.set(session_key(session_id), value, ttl=_SECTIONS.get(section, DEFAULT_TTL_SECONDS),
                            namespace=namespace(section))
        else:
            await cache.delete(session_key(session_id), namespace=namespace(section))
    except Exception as exc:
        logger.warning("session_state: write of %s failed: %s", section, exc)


def remember(ctx_or_deps: Any, section: str, **fields: Any) -> None:
    """Merge fields into a section for this session; a field set to None is removed.

    Call from inside a tool (sync or async). Only the in-memory turn state changes here; the chat turn saves
    changed sections once, together with the message history (save_changed_state).
    """
    deps = _deps_of(ctx_or_deps)
    state = getattr(deps, "session_state", None)
    if state is None:
        return
    current = state.get(section, {})
    merged = {k: v for k, v in {**current, **fields}.items() if v is not None}
    if merged != current:
        state[section] = merged
        deps.session_state_changed.add(section)


def forget(ctx_or_deps: Any, section: str) -> None:
    deps = _deps_of(ctx_or_deps)
    state = getattr(deps, "session_state", None)
    if state is not None and section in state:
        del state[section]
        deps.session_state_changed.add(section)


async def save_changed_state(deps: Any) -> None:
    changed = getattr(deps, "session_state_changed", set())
    for section in sorted(changed):
        await set_section(deps.session_id, section, deps.session_state.get(section, {}))
    changed.clear()


def render_state(state: dict[str, dict[str, Any]]) -> str:
    """Compact text of every section for the user message; empty when nothing is remembered."""
    shown = {}
    for name, value in state.items():
        view = _VIEWS.get(name)
        rendered = view(value) if view else value
        if rendered:
            shown[name] = rendered
    return json.dumps(shown, ensure_ascii=False, separators=(",", ":"), default=str) if shown else ""
