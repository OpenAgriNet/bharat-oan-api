"""The last place the farmer named in a chat, saved when the place name is geocoded.

Tools that work for a location (IMD agromet advisory) use it when no coordinates are passed, so a follow-up such
as "Maize" after "What's the weather in Hazaribagh?" stays on Hazaribagh instead of the browser location.
"""
from typing import Optional, Tuple

from app.core.cache import cache
from helpers.utils import get_logger

logger = get_logger(__name__)

NAMED_PLACE_NAMESPACE = "chat-named-place"
NAMED_PLACE_TTL_SECONDS = 24 * 3600
SAME_POINT_DEGREES = 0.01  # the model may round the coordinates it copies from forward_geocode


async def remember_place(session_id: str, latitude: float, longitude: float, kind: Optional[str] = None) -> None:
    """kind is the geocoder's place type, e.g. "city", "district" or "state"."""
    try:
        await cache.set(session_id, [latitude, longitude, kind], ttl=NAMED_PLACE_TTL_SECONDS,
                        namespace=NAMED_PLACE_NAMESPACE)
    except Exception as exc:  # the chat still works; only the follow-up falls back to the browser location
        logger.warning(f"Named place not saved for session {session_id}: {exc}")


async def _stored(session_id: str) -> Optional[list]:
    try:
        return await cache.get(session_id, namespace=NAMED_PLACE_NAMESPACE)
    except Exception as exc:
        logger.warning(f"Named place not read for session {session_id}: {exc}")
        return None


async def named_place(session_id: str) -> Optional[Tuple[float, float]]:
    value = await _stored(session_id)
    return (value[0], value[1]) if value else None


async def is_state_point(session_id: str, latitude: float, longitude: float) -> bool:
    """True when these coordinates are (about 1 km around) the last named place and that place is a whole state."""
    value = await _stored(session_id)
    return bool(value and len(value) > 2 and value[2] == "state"
                and abs(value[0] - latitude) < SAME_POINT_DEGREES and abs(value[1] - longitude) < SAME_POINT_DEGREES)
