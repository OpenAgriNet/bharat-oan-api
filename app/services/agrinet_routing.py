from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Literal

import httpx

from agents.model_registry import get_registry
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
    "capacity_deflect",
    "routing_disabled",
]

_METRICS_TIMEOUT_SECONDS = 2.0
_NUM_RE = re.compile(
    r"^(vllm:num_requests_running|vllm:num_requests_waiting)\{.*\}\s+([\d.eE+-]+)$"
)


@dataclass(frozen=True)
class AgrinetRouteDecision:
    route: AgrinetRoute
    model_name: str
    source: AgrinetRouteSource


def _route_key(session_id: str) -> str:
    return f"{session_id}_{AGRINET_ROUTE_SUFFIX}"


def _route_ttl_seconds() -> int:
    return get_registry().get_routing_ttl("agrinet") or DEFAULT_CACHE_TTL


def _build_decision(route: AgrinetRoute, source: AgrinetRouteSource) -> AgrinetRouteDecision:
    return AgrinetRouteDecision(
        route=route,
        model_name=get_agrinet_route_model_name(route),
        source=source,
    )


async def get_stored_agrinet_route(session_id: str) -> AgrinetRoute | None:
    cached_route = await get_cache(_route_key(session_id))
    valid_aliases = set(get_registry().get_use_case_aliases("agrinet"))
    if cached_route in valid_aliases:
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


async def _fetch_concurrency(alias: str) -> int | None:
    """Sum of running + waiting requests on a vLLM engine for the given alias, or None on failure."""
    metrics_url = get_registry().get_metrics_url(alias)
    if not metrics_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_METRICS_TIMEOUT_SECONDS) as client:
            response = await client.get(metrics_url)
            response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("Failed to fetch metrics for alias '%s' from %s: %s", alias, metrics_url, exc)
        return None

    total = 0
    for line in response.text.splitlines():
        match = _NUM_RE.match(line)
        if match:
            total += int(float(match.group(2)))
    return total


async def get_concurrency(alias: str) -> int | None:
    """Cached (short-TTL, shared via Redis) read of a vLLM alias's current concurrency."""
    cache_key = f"concurrency_{alias}"
    try:
        cached = await get_cache(cache_key)
    except Exception as exc:
        logger.warning("Concurrency cache read failed for alias '%s': %s", alias, exc)
        cached = None

    if cached is not None:
        return cached

    concurrency = await _fetch_concurrency(alias)
    if concurrency is not None:
        ttl = get_registry().get_metrics_cache_ttl(alias)
        try:
            await set_cache(cache_key, concurrency, ttl=ttl)
        except Exception as exc:
            logger.warning("Concurrency cache write failed for alias '%s': %s", alias, exc)
    return concurrency


async def _apply_capacity_gate(
    route: AgrinetRoute,
    source: AgrinetRouteSource,
    session_id: str,
) -> AgrinetRouteDecision:
    """Deflect this turn to the default route when the assigned alias is saturated.

    Session stickiness in Redis is deliberately left untouched — saturation is
    transient, so rewriting it here would drain the canary cohort one turn at a
    time and quietly skew the split.
    """
    max_concurrency = get_registry().get_fallback_on_concurrency(route)
    if max_concurrency is None:
        return _build_decision(route, source)

    concurrency = await get_concurrency(route)
    if concurrency is not None and concurrency < max_concurrency:
        return _build_decision(route, source)

    default_alias = get_registry().get_default_alias("agrinet")
    logger.info(
        "Alias '%s' at capacity (concurrency=%s, max=%s); deflecting session %s to '%s' for this turn",
        route,
        concurrency,
        max_concurrency,
        session_id,
        default_alias,
    )
    return _build_decision(default_alias, "capacity_deflect")


def choose_weighted_agrinet_route(
    randint_fn: Callable[[int, int], int] = random.randint,
) -> AgrinetRoute:
    registry = get_registry()
    aliases = registry.get_use_case_aliases("agrinet")
    proportions = registry.get_use_case_proportions("agrinet")

    total_weight = sum(proportions)
    roll = randint_fn(1, total_weight)

    cumulative = 0
    for alias, weight in zip(aliases, proportions):
        cumulative += weight
        if roll <= cumulative:
            return alias
    return aliases[-1]


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
        return await _apply_capacity_gate(stored_route, "redis", session_id)

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
    return await _apply_capacity_gate(selected_route, "session_start_weighted", session_id)


def get_alternate_agrinet_route(route: AgrinetRoute) -> AgrinetRoute:
    """Return the fallback alias for the given route, as defined in config/models.yaml."""
    fallback = get_registry().get_fallback_alias(route)
    return fallback if fallback else AGRINET_DEFAULT_ROUTE
