"""Knowledge Provider POST /search client.

Authenticates as a Keycloak service account (client-credentials grant) and caches
the token until shortly before it expires. The advisory and scheme tools call
`search` with a KP search body (`search_profile` advisory or scheme).
"""
import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx

from app.config import DEFAULT_HTTP_TIMEOUT

# Refresh this many seconds before Keycloak's expiry so a token never lapses mid-request.
_TOKEN_EXPIRY_MARGIN = 30

_token: Optional[str] = None
_token_expires_at = 0.0
_token_lock = asyncio.Lock()


class KnowledgeProviderError(Exception):
    """KP search failed: not configured, auth refused, non-200 or non-JSON response."""


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def search_url() -> str:
    base = _env("KNOWLEDGE_PROVIDER_URL")
    return base.rstrip("/") + "/search" if base else ""


async def _access_token(client: httpx.AsyncClient, refresh: bool) -> str:
    global _token, _token_expires_at
    async with _token_lock:
        if _token and not refresh and time.monotonic() < _token_expires_at:
            return _token
        token_url = _env("KNOWLEDGE_PROVIDER_TOKEN_URL")
        client_id = _env("KNOWLEDGE_PROVIDER_CLIENT_ID")
        client_secret = _env("KNOWLEDGE_PROVIDER_CLIENT_SECRET")
        if not (token_url and client_id and client_secret):
            raise KnowledgeProviderError("Knowledge Provider credentials are not configured")
        response = await client.post(
            token_url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise KnowledgeProviderError(f"Knowledge Provider token request returned status {response.status_code}")
        body = response.json()
        _token = body["access_token"]
        _token_expires_at = time.monotonic() + max(int(body.get("expires_in", 60)) - _TOKEN_EXPIRY_MARGIN, 0)
        return _token


async def search(body: Dict[str, Any]) -> Dict[str, Any]:
    """POST `body` to KP /search and return the JSON object.

    Raises KnowledgeProviderError for configuration, auth and response problems, and
    lets httpx transport errors (including timeouts) propagate.
    """
    url = search_url()
    if not url:
        raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_URL is not set")
    network_id = _env("KNOWLEDGE_PROVIDER_NETWORK_ID")
    if network_id:
        body = {**body, "network_id": network_id}
    async with httpx.AsyncClient() as client:
        # A 401 means the cached token was revoked or expired early: fetch a new one once.
        for refresh in (False, True):
            token = await _access_token(client, refresh)
            response = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=DEFAULT_HTTP_TIMEOUT
            )
            if response.status_code != 401:
                break
    if response.status_code != 200:
        raise KnowledgeProviderError(
            f"Knowledge Provider search returned status {response.status_code}: {(response.text or '')[:300]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise KnowledgeProviderError("Knowledge Provider search returned a non-JSON body") from exc
    if not isinstance(data, dict):
        raise KnowledgeProviderError("Knowledge Provider search returned a non-object body")
    return data


def hit_score(hit: Dict[str, Any], data: Dict[str, Any]) -> float:
    """The score KP ranked by (`effective_config.ranking_score_field`, e.g. `_advisory_score`)."""
    field = (data.get("effective_config") or {}).get("ranking_score_field") or "_score"
    try:
        return float(hit.get(field, hit.get("_score")) or 0)
    except (TypeError, ValueError):
        return 0.0


def context_strings(context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """KP's `advisory_search` / `scheme_search` object as the string map the tools read
    (booleans as "true"/"false", nulls dropped), matching the old Beckn tag values."""
    out: Dict[str, str] = {}
    for key, value in (context or {}).items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out
