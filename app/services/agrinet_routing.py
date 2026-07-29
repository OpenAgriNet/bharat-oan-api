from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Literal

import httpx

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

GEMMA_METRICS_CACHE_KEY = "gemma_concurrency"
_GEMMA_METRICS_TIMEOUT_SECONDS = 2.0
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


def get_gemma_metrics_url() -> str:
    """vLLM metrics endpoint, derived from the Gemma base URL unless overridden."""
    override = (settings.agrinet_gemma_metrics_url or "").strip()
    if override:
        return override
    base_url = (settings.agrinet_gemma_base_url or "").strip()
    return re.sub(r"/v1/?$", "", base_url) + "/metrics"


async def _fetch_gemma_concurrency() -> int | None:
    """Sum of running + waiting requests on the Gemma vLLM engine(s), or None on failure."""
    metrics_url = get_gemma_metrics_url()
    try:
        async with httpx.AsyncClient(timeout=_GEMMA_METRICS_TIMEOUT_SECONDS) as client:
            response = await client.get(metrics_url)
            response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("Failed to fetch Gemma metrics from %s: %s", metrics_url, exc)
        return None

    total = 0
    for line in response.text.splitlines():
        match = _NUM_RE.match(line)
        if match:
            total += int(float(match.group(2)))
    return total


async def get_gemma_concurrency() -> int | None:
    """Cached (short-TTL, shared via Redis) read of Gemma's current concurrency.

    Redis errors are swallowed - a cache outage should degrade to a direct
    metrics fetch per request, not break routing (and therefore chat turns).
    """
    try:
        cached = await get_cache(GEMMA_METRICS_CACHE_KEY)
    except Exception as exc:
        logger.warning("Gemma concurrency cache read failed: %s", exc)
        cached = None

    if cached is not None:
        return cached

    concurrency = await _fetch_gemma_concurrency()
    if concurrency is not None:
        try:
            await set_cache(
                GEMMA_METRICS_CACHE_KEY,
                concurrency,
                ttl=settings.agrinet_gemma_metrics_cache_ttl,
            )
        except Exception as exc:
            logger.warning("Gemma concurrency cache write failed: %s", exc)
    return concurrency


async def _apply_capacity_gate(
    route: AgrinetRoute,
    source: AgrinetRouteSource,
    session_id: str,
) -> AgrinetRouteDecision:
    """Deflect this turn to the default route while Gemma is saturated.

    The session's stored route is deliberately left untouched. Unlike `failover`,
    saturation is transient, so rewriting Redis here would drain the canary cohort
    one turn at a time under sustained load and quietly skew the split.
    """
    if route != "gemma":
        return _build_decision(route, source)

    concurrency = await get_gemma_concurrency()
    if concurrency is not None and concurrency < settings.agrinet_gemma_max_concurrency:
        return _build_decision(route, source)

    logger.info(
        "Gemma at capacity (concurrency=%s, max=%s); deflecting session %s to %s for this turn",
        concurrency,
        settings.agrinet_gemma_max_concurrency,
        session_id,
        AGRINET_DEFAULT_ROUTE,
    )
    return _build_decision(AGRINET_DEFAULT_ROUTE, "capacity_deflect")


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
    return "gemma" if route == "gpt41" else "gpt41"
