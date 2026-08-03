"""Process-local + Redis scheme catalog store and background warmer."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.config import settings
from app.core.cache import cache
from helpers.scheme_catalog.client import fetch_catalog_snapshot, fetch_catalog_version
from helpers.scheme_catalog.fallback import builtin_catalog_snapshot
from helpers.scheme_catalog.models import CatalogSnapshot
from helpers.utils import get_logger

logger = get_logger(__name__)

SCHEME_CATALOG_NS = "scheme-catalog"
_SNAPSHOT_KEY = "snapshot"
_VERSION_KEY = "version"

_local_snapshot: Optional[CatalogSnapshot] = None
_warmer_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


def _enabled() -> bool:
    return bool(settings.scheme_catalog_enabled)


def get_local_snapshot() -> Optional[CatalogSnapshot]:
    return _local_snapshot


def pin_catalog_snapshot() -> CatalogSnapshot:
    """Return a deep copy of the active snapshot for one chat turn.

    Never raises; falls back to builtin registry when catalog is empty/disabled.
    """
    snap = _local_snapshot
    if snap is None or not snap.schemes:
        snap = builtin_catalog_snapshot()
    return CatalogSnapshot.model_validate(snap.model_dump())


def get_active_scheme_list(
    snapshot: Optional[CatalogSnapshot] = None,
) -> list[dict[str, Any]]:
    """Vector scheme list for search allow-list / resolve."""
    snap = snapshot or pin_catalog_snapshot()
    items = snap.scheme_list_for_search()
    if items:
        return items
    return builtin_catalog_snapshot().scheme_list_for_search()


async def _write_redis(snapshot: CatalogSnapshot) -> None:
    ttl = settings.scheme_catalog_cache_ttl
    try:
        await cache.set(
            _SNAPSHOT_KEY,
            snapshot.model_dump(),
            ttl=ttl,
            namespace=SCHEME_CATALOG_NS,
        )
        await cache.set(
            _VERSION_KEY,
            snapshot.catalog_version,
            ttl=ttl,
            namespace=SCHEME_CATALOG_NS,
        )
    except Exception as exc:
        logger.warning("Failed to write scheme catalog to Redis: %s", exc)


async def _read_redis() -> Optional[CatalogSnapshot]:
    try:
        raw = await cache.get(_SNAPSHOT_KEY, namespace=SCHEME_CATALOG_NS)
        if not raw:
            return None
        if isinstance(raw, dict):
            snap = CatalogSnapshot.model_validate(raw)
            snap.source = "redis"
            return snap
    except Exception as exc:
        logger.warning("Failed to read scheme catalog from Redis: %s", exc)
    return None


async def refresh_scheme_catalog(*, force: bool = False) -> CatalogSnapshot:
    """Refresh process-local cache from API (if version changed) or Redis / builtin."""
    global _local_snapshot

    if not _enabled():
        snap = builtin_catalog_snapshot()
        _local_snapshot = snap
        logger.info("Scheme catalog disabled; using builtin list (%s schemes)", len(snap.schemes))
        return snap

    local_version = _local_snapshot.catalog_version if _local_snapshot else None

    if not force and _local_snapshot and _local_snapshot.schemes:
        remote_version = await fetch_catalog_version()
        if remote_version is not None and local_version is not None and remote_version == local_version:
            return _local_snapshot

    remote = await fetch_catalog_snapshot()
    if remote and remote.schemes:
        _local_snapshot = remote
        await _write_redis(remote)
        logger.info(
            "Scheme catalog refreshed from API: version=%s schemes=%s source=%s",
            remote.catalog_version,
            len(remote.schemes),
            remote.source,
        )
        return remote

    # API miss: try Redis, then keep last good, then builtin
    if force or _local_snapshot is None:
        cached = await _read_redis()
        if cached and cached.schemes:
            _local_snapshot = cached
            logger.info(
                "Scheme catalog loaded from Redis: version=%s schemes=%s",
                cached.catalog_version,
                len(cached.schemes),
            )
            return cached

    if _local_snapshot and _local_snapshot.schemes:
        logger.warning("Scheme catalog refresh failed; keeping last-good version=%s", local_version)
        return _local_snapshot

    snap = builtin_catalog_snapshot()
    _local_snapshot = snap
    logger.warning("Scheme catalog using builtin fallback (%s schemes)", len(snap.schemes))
    return snap


async def _warmer_loop(stop_event: asyncio.Event) -> None:
    interval = max(15, int(settings.scheme_catalog_refresh_seconds or 300))
    logger.info("Scheme catalog warmer started (interval=%ss)", interval)
    while not stop_event.is_set():
        try:
            await refresh_scheme_catalog(force=False)
        except Exception as exc:
            logger.exception("Scheme catalog warmer iteration failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("Scheme catalog warmer stopped")


def start_catalog_warmer() -> None:
    """Schedule background catalog refresh; safe to call when disabled."""
    global _warmer_task, _stop_event
    if not _enabled():
        return
    if _warmer_task and not _warmer_task.done():
        return
    _stop_event = asyncio.Event()
    _warmer_task = asyncio.create_task(_warmer_loop(_stop_event))


async def stop_catalog_warmer() -> None:
    global _warmer_task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _warmer_task:
        try:
            await asyncio.wait_for(_warmer_task, timeout=5)
        except Exception:
            _warmer_task.cancel()
        _warmer_task = None
    _stop_event = None
