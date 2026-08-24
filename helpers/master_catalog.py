"""
Master Catalog (Postgres, owned by docs-pipeline) — Redis snapshot reader.

docs-pipeline writes `master-catalog:dev:snapshot` / `master-catalog:live:snapshot`
(raw JSON, no aiocache prefix — see docs-pipeline's ENV.md "Master Catalog") every
time a document is DEV-ingested or PROD-promoted. This module is the read side:
it lets the chat agent see newly-live schemes without a redeploy.

Fails open by design: any Redis error, missing key, or malformed payload returns
None from get_master_catalog_snapshot(), and callers (see
scheme_qdrant_search.get_builtin_scheme_list) treat that as "no vector-indexed
schemes known right now" rather than raising — a Redis outage degrades scheme
routing instead of crashing it.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.config import settings
from helpers.utils import get_logger

logger = get_logger(__name__)

_REDIS_KEY_PREFIX = "master-catalog"
_DEV_ENVIRONMENTS = ("local", "dev", "development")

_redis_client = None
_redis_client_initialized = False

# Last version number seen per tier, so a sync from docs-pipeline can be
# logged exactly once when it's first noticed — not on every single call
# (this function runs multiple times per chat turn).
_last_seen_version: dict[str, Optional[int]] = {}


def _get_redis_client():
    """Raw redis-py client against the same Redis instance as app.core.cache,
    but WITHOUT its aiocache key prefix — docs-pipeline writes unprefixed keys."""
    global _redis_client, _redis_client_initialized
    if _redis_client_initialized:
        return _redis_client

    _redis_client_initialized = True
    try:
        import redis

        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            socket_timeout=settings.redis_socket_timeout,
        )
    except Exception as exc:
        logger.warning("master_catalog: could not create Redis client: %s", exc)
        _redis_client = None
    return _redis_client


def _tier_for_environment() -> str:
    return "dev" if settings.environment.strip().lower() in _DEV_ENVIRONMENTS else "live"


def get_master_catalog_snapshot(tier: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Read+parse the live snapshot for this deployment's tier (dev sees dev+live
    entries, prod sees live only — decided by docs-pipeline at write time).

    Returns None on any failure (no client, connection error, missing key,
    malformed JSON) — never raises. Callers must have a fallback path.
    """
    tier = tier or _tier_for_environment()
    client = _get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_REDIS_KEY_PREFIX}:{tier}:snapshot")
        if not raw:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        _log_if_new_sync(tier, payload)
        return payload
    except Exception as exc:
        logger.warning("master_catalog: snapshot read failed (tier=%s): %s", tier, exc)
        return None


def _log_if_new_sync(tier: str, payload: dict[str, Any]) -> None:
    """This process notices a docs-pipeline sync the next time it happens to
    read Redis (there's no push/pub-sub — this is pull-based), so it's not
    instantaneous, but it fires exactly once per new version, not on every
    read. Logs the full latest entry set so a sync is visible without a
    Redis GUI or hitting /health/master-catalog by hand."""
    version = payload.get("version")
    if _last_seen_version.get(tier) == version:
        return
    _last_seen_version[tier] = version

    entries = payload.get("entries")
    entries = entries if isinstance(entries, list) else []
    logger.info(
        "REDIS SYNC DETECTED: tier=%s version=%s updated_at=%s entry_count=%d",
        tier, version, payload.get("updated_at"), len(entries),
    )
    for e in entries:
        if not isinstance(e, dict):
            continue
        logger.info(
            "  synced entry: code=%s name=%s tool_name=%s status=%s aliases=%s",
            e.get("code"), e.get("name"), e.get("tool_name"), e.get("status"), e.get("aliases"),
        )


def get_vector_scheme_entries(tier: Optional[str] = None) -> list[dict[str, Any]]:
    """Entries routed to search_schemes (vector-indexed/Qdrant) only — legacy
    get_scheme_info schemes are a separate, still-static list and must never
    come from here."""
    snapshot = get_master_catalog_snapshot(tier)
    if not snapshot:
        return []
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("tool_name") == "search_schemes"]


def get_vector_schemes_prompt_block(tier: Optional[str] = None) -> dict[str, Any]:
    """Pre-rendered system-prompt fragments docs-pipeline built for this snapshot.
    Empty strings/zero count when the snapshot is unavailable — callers fall back
    to their own static rendering in that case."""
    snapshot = get_master_catalog_snapshot(tier)
    prompt = (snapshot or {}).get("prompt") if snapshot else None
    if not isinstance(prompt, dict):
        return {"vector_schemes_bullets": "", "vector_schemes_identifiers": "", "vector_scheme_count": 0}
    return prompt
