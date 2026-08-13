from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Awaitable, Callable, Generic, TypeVar

from agents.model_registry import ModelRegistry, get_registry

logger = logging.getLogger(__name__)

T = TypeVar("T")
ModelRunner = Callable[[str, Any], Awaitable[T]]
FallbackGuard = Callable[[], bool]


@dataclass(frozen=True)
class ModelExecutionResult(Generic[T]):
    value: T
    alias: str
    model_name: str
    primary_alias: str
    attempted_aliases: tuple[str, ...]

    @property
    def fallback_used(self) -> bool:
        return self.alias != self.primary_alias

    @property
    def fallback_from(self) -> str | None:
        return self.primary_alias if self.fallback_used else None


class ModelService:
    """Execute any configured LLM use case with one timeout/fallback policy."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or get_registry()

    async def run(
        self,
        use_case: str,
        runner: ModelRunner[T],
        *,
        primary_alias: str | None = None,
        can_fallback: FallbackGuard | None = None,
    ) -> ModelExecutionResult[T]:
        primary = primary_alias or self.registry.get_default_alias(use_case)
        if not primary:
            raise ValueError(f"Use case '{use_case}' has no primary model alias")

        chain = self.registry.get_fallback_chain(use_case, primary)
        timeout = self.registry.get_timeout(use_case)
        attempted: list[str] = []

        for index, alias in enumerate(chain):
            attempted.append(alias)
            try:
                model = self.registry.get_model(alias)
                value = await asyncio.wait_for(
                    runner(alias, model),
                    timeout=timeout,
                )
                return ModelExecutionResult(
                    value=value,
                    alias=alias,
                    model_name=self.registry.get_model_name(alias),
                    primary_alias=primary,
                    attempted_aliases=tuple(attempted),
                )
            except Exception as exc:
                has_fallback = index + 1 < len(chain)
                fallback_allowed = can_fallback is None or can_fallback()
                if not has_fallback or not fallback_allowed:
                    if has_fallback and not fallback_allowed:
                        logger.warning(
                            "Use case '%s' failed on alias '%s', but fallback is no longer safe: %s",
                            use_case,
                            alias,
                            exc,
                        )
                    raise

                logger.warning(
                    "Use case '%s' failed on alias '%s'; retrying on '%s': %s",
                    use_case,
                    alias,
                    chain[index + 1],
                    exc,
                )

        raise RuntimeError(f"Use case '{use_case}' has no executable model aliases")


@lru_cache(maxsize=1)
def get_model_service() -> ModelService:
    return ModelService()
