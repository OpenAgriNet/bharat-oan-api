from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Literal

from agents.models import AGRINET_DEFAULT_ROUTE, AgrinetRoute, get_agrinet_route_model_name
from app.config import settings
from app.utils import DEFAULT_CACHE_TTL, get_cache, set_cache
from helpers.utils import get_logger

logger = get_logger(__name__)

AGRINET_ROUTE_SUFFIX = "AGRINET_ROUTE"
AgrinetRouteSource = Literal[
    "redis",
    "session_start_weighted",
    "state_repair",
    "failover",
    "routing_disabled",
]


@dataclass(frozen=True)
class AgrinetRouteDecision:
    route: AgrinetRoute
    model_name: str
    source: AgrinetRouteSource


def _route_key(session_id: str) -> str:
    return f"{session_id}_{AGRINET_ROUTE_SUFFIX}"


def _route_ttl_seconds() -> int:
    return settings.agrinet_route_ttl_seconds or DEFAULT_CACHE_TTL


def _build_decision(route: AgrinetRoute, source: AgrinetRouteSource) -> AgrinetRouteDecision:
    return AgrinetRouteDecision(
        route=route,
        model_name=get_agrinet_route_model_name(route),
        source=source,
    )


async def get_stored_agrinet_route(session_id: str) -> AgrinetRoute | None:
    cached_route = await get_cache(_route_key(session_id))
    if cached_route in ("gpt41", "gemma"):
        return cached_route
    if cached_route is not None:
        logger.warning(
            "Ignoring invalid agrinet route %r for session %s",
            cached_route,
            session_id,
        )
    return None


async def set_session_agrinet_route(
    session_id: str,
    route: AgrinetRoute,
    *,
    ttl_seconds: int | None = None,
) -> bool:
    return await set_cache(
        _route_key(session_id),
        route,
        ttl=ttl_seconds or _route_ttl_seconds(),
    )


async def refresh_session_agrinet_route_ttl(session_id: str) -> bool:
    route = await get_stored_agrinet_route(session_id)
    if not route:
        return False
    return await set_session_agrinet_route(session_id, route)


def choose_weighted_agrinet_route(
    randint_fn: Callable[[int, int], int] = random.randint,
) -> AgrinetRoute:
    total_weight = settings.agrinet_route_gpt41_weight + settings.agrinet_route_gemma_weight
    roll = randint_fn(1, total_weight)
    if roll <= settings.agrinet_route_gpt41_weight:
        return "gpt41"
    return "gemma"


async def resolve_agrinet_route(
    session_id: str,
    *,
    has_history: bool,
    randint_fn: Callable[[int, int], int] = random.randint,
) -> AgrinetRouteDecision:
    if not settings.agrinet_routing_enabled:
        return _build_decision(AGRINET_DEFAULT_ROUTE, "routing_disabled")

    stored_route = await get_stored_agrinet_route(session_id)
    if stored_route:
        return _build_decision(stored_route, "redis")

    if has_history:
        logger.warning(
            "Agrinet route missing for existing session %s; repairing to %s",
            session_id,
            AGRINET_DEFAULT_ROUTE,
        )
        await set_session_agrinet_route(session_id, AGRINET_DEFAULT_ROUTE)
        return _build_decision(AGRINET_DEFAULT_ROUTE, "state_repair")

    selected_route = choose_weighted_agrinet_route(randint_fn=randint_fn)
    await set_session_agrinet_route(session_id, selected_route)
    return _build_decision(selected_route, "session_start_weighted")


def get_alternate_agrinet_route(route: AgrinetRoute) -> AgrinetRoute:
    return "gemma" if route == "gpt41" else "gpt41"
