"""Shared session routing and whole-run fallback for every agent.

Model aliases own fallback policy; use cases own weights and per-attempt deadlines.
Streaming callers must prevent retries once output has reached the client.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Awaitable, Callable, TypeVar

import httpx

from agents.model_registry import ModelRegistry, get_registry

logger = logging.getLogger(__name__)
T = TypeVar("T")
_METRIC = re.compile(r"^(vllm:num_requests_(?:running|waiting))(?:\{.*\})?\s+(\S+)(?:\s+\S+)?$")


@dataclass(frozen=True)
class ModelRouteDecision:
    route: str
    model_name: str
    source: str


def parse_concurrency(text: str) -> float | None:
    samples = {}
    for line in text.splitlines():
        match = _METRIC.fullmatch(line.strip())
        if match:
            try:
                value = float(match[2])
            except ValueError:
                return None
            if not math.isfinite(value) or value < 0:
                return None
            samples[match[1]] = samples.get(match[1], 0) + value
    return sum(samples.values()) if len(samples) == 2 else None


class ModelRouter:
    def __init__(self, registry: ModelRegistry, cache) -> None:
        self.registry = registry
        self.cache = cache

    def decision(self, alias: str, source: str) -> ModelRouteDecision:
        return ModelRouteDecision(alias, self.registry.get_model_name(alias), source)

    def _key(self, use_case: str, session_id: str) -> str:
        # Preserve deployed Agrinet session keys; isolate every other use case.
        return f"{session_id}_{use_case.upper()}_ROUTE"

    async def store(self, use_case: str, session_id: str, alias: str) -> None:
        await self.cache.set(self._key(use_case, session_id), alias,
                             ttl=self.registry.get_routing_ttl(use_case))

    async def concurrency(self, alias: str) -> float | None:
        key = f"concurrency_{alias}"
        try:
            cached = await self.cache.get(key)
            if isinstance(cached, (int, float)) and math.isfinite(cached) and cached >= 0:
                return cached
        except Exception:
            logger.warning("Could not read concurrency cache for %s", alias)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(self.registry.get_metrics_url(alias))
                response.raise_for_status()
            value = parse_concurrency(response.text)
        except httpx.HTTPError:
            logger.warning("Could not read vLLM metrics for %s", alias)
            return None
        if value is not None:
            try:
                await self.cache.set(key, value, ttl=self.registry.get_metrics_cache_ttl(alias))
            except Exception:
                logger.warning("Could not cache concurrency for %s", alias)
        return value

    async def _capacity(self, decision: ModelRouteDecision) -> ModelRouteDecision:
        seen = set()
        while decision.route not in seen:
            seen.add(decision.route)
            threshold = self.registry.get_fallback_on_concurrency(decision.route)
            target = self.registry.get_fallback_alias(decision.route)
            if threshold is None or not target:
                return decision
            value = await self.concurrency(decision.route)
            if value is not None and value <= threshold:
                return decision
            # Missing metrics are treated as unavailable capacity, never as zero load.
            decision = self.decision(target, "capacity_deflect")
        raise ValueError("Fallback cycle in capacity routing")

    async def resolve(self, use_case: str, session_id: str, *, has_history: bool = False,
                      randint: Callable[[int, int], int] = random.randint) -> ModelRouteDecision:
        registry = self.registry
        alias = await self.cache.get(self._key(use_case, session_id))
        if not registry.routing_enabled(use_case):
            alias, source = registry.get_default_alias(use_case), "routing_disabled"
        elif alias in registry.get_reachable_aliases(use_case):
            source = "redis"
        elif has_history:
            alias, source = registry.get_default_alias(use_case), "state_repair"
        else:
            roll = randint(1, 100)
            for alias, weight in zip(registry.get_use_case_aliases(use_case),
                                     registry.get_use_case_proportions(use_case)):
                roll -= weight
                if roll <= 0:
                    break
            source = "session_start_weighted"
        await self.store(use_case, session_id, alias)
        return await self._capacity(self.decision(alias, source))

    async def run(self, use_case: str, session_id: str,
                  attempt: Callable[[ModelRouteDecision], Awaitable[T]], *,
                  decision: ModelRouteDecision | None = None,
                  can_fallback: Callable[[], bool] = lambda: True) -> tuple[T, ModelRouteDecision, bool]:
        """Run a complete agent attempt, including streaming/tool calls, under one deadline.

        SDK FallbackModel only retries opening a stream; this boundary also covers
        stream consumption and caller-visible output. Cancelled requests never retry.
        """
        decision = decision or await self.resolve(use_case, session_id)
        seen = set()
        error_assignment = None
        while decision.route not in seen:
            seen.add(decision.route)
            try:
                async with asyncio.timeout(self.registry.get_timeout(use_case)):
                    result = await attempt(decision)
            except self.registry.get_fallback_errors(decision.route) as exc:
                target = self.registry.get_fallback_alias(decision.route)
                if not target or target in seen or not can_fallback():
                    raise
                logger.warning("%s alias %s failed (%s); falling back to %s",
                               use_case, decision.route, type(exc).__name__, target)
                error_assignment = target
                decision = await self._capacity(self.decision(target, "failover"))
                continue
            if error_assignment:
                # Error fallback sticks; capacity-only deflection never rewrites the assignment.
                await self.store(use_case, session_id, error_assignment)
            return result, decision, error_assignment is not None
        raise ValueError("Fallback cycle in model execution")

    async def run_agent(self, use_case: str, session_id: str, agent, *args, **kwargs):
        """Ordinary agents need only this call; custom streaming uses run() above."""
        async def attempt(decision):
            return await agent.run(*args, model=self.registry.get_model(decision.route), **kwargs)
        return await self.run(use_case, session_id, attempt)


@lru_cache(maxsize=1)
def get_model_router() -> ModelRouter:
    from app.core.cache import cache
    return ModelRouter(get_registry(), cache)
