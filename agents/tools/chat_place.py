"""The last place the farmer named in a chat, saved in session state when the place name is geocoded.

Tools that work for a location (IMD agromet advisory) use it when no coordinates are passed, so a follow-up such
as "Maize" after "What's the weather in Hazaribagh?" stays on Hazaribagh instead of the browser location.
The model also sees it in the session state, so it can reuse the coordinates instead of geocoding again.
"""
from typing import Any, Optional, Tuple

from app.services.session_state import register_section, remember

PLACE = register_section("place")
SAME_POINT_DEGREES = 0.01  # the model may round the coordinates it copies from forward_geocode


async def remember_place(ctx: Any, name: str, latitude: float, longitude: float, kind: Optional[str] = None) -> None:
    """kind is the geocoder's place type, e.g. "city", "district" or "state"."""
    remember(ctx, PLACE, name=name, latitude=latitude, longitude=longitude, kind=kind)


def _stored(ctx: Any) -> dict:
    deps = getattr(ctx, "deps", ctx)
    return (getattr(deps, "session_state", None) or {}).get(PLACE) or {}


async def named_place(ctx: Any) -> Optional[Tuple[float, float]]:
    value = _stored(ctx)
    return (value["latitude"], value["longitude"]) if "latitude" in value else None


async def is_state_point(ctx: Any, latitude: float, longitude: float) -> bool:
    """True when these coordinates are (about 1 km around) the last named place and that place is a whole state."""
    value = _stored(ctx)
    return bool(value.get("kind") == "state"
                and abs(value["latitude"] - latitude) < SAME_POINT_DEGREES
                and abs(value["longitude"] - longitude) < SAME_POINT_DEGREES)
