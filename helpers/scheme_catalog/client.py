"""HTTP client for the master scheme catalog API."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import settings
from helpers.scheme_catalog.models import CatalogScheme, CatalogSnapshot
from helpers.utils import get_logger

logger = get_logger(__name__)


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    key = (settings.scheme_catalog_service_key or "").strip()
    if key:
        headers["X-Catalog-Service-Key"] = key
    return headers


def _base_url() -> str:
    return (settings.scheme_catalog_url or "").rstrip("/")


async def fetch_catalog_version() -> Optional[int]:
    """Cheap poll: GET .../version → integer version."""
    base = _base_url()
    if not base:
        return None
    url = base if base.endswith("/version") else f"{base}/version"
    # Allow callers that set snapshot URL: derive version path
    if base.endswith("/snapshot"):
        url = base[: -len("/snapshot")] + "/version"
    try:
        async with httpx.AsyncClient(timeout=settings.scheme_catalog_http_timeout) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 404:
                # Some publishers expose only /snapshot
                return None
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return int(data.get("version") or data.get("catalog_version") or 0)
            if isinstance(data, int):
                return int(data)
    except Exception as exc:
        logger.warning("Scheme catalog version poll failed: %s", exc)
    return None


def _parse_scheme_row(raw: Any) -> Optional[CatalogScheme]:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or raw.get("scheme_code") or "").strip()
    name = str(raw.get("name") or raw.get("scheme_name") or "").strip()
    if not code or not name:
        return None
    status = str(raw.get("status") or "live").strip().lower()
    if status and status not in ("live", "prod", "active", "published"):
        # API may already filter; still skip non-live if present
        if status in ("dev", "disabled", "pending", "pending_reindex", "pending_prod"):
            return None
    aliases = raw.get("aliases") or raw.get("scheme_aliases") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",") if a.strip()]
    if not isinstance(aliases, list):
        aliases = []
    return CatalogScheme(
        code=code,
        name=name,
        tool_name=str(raw.get("tool_name") or raw.get("tool") or "search_schemes").strip()
        or "search_schemes",
        content_type=str(raw.get("content_type") or "scheme"),
        doc_id=raw.get("doc_id"),
        prompt_snippet=raw.get("prompt_snippet"),
        status=status or "live",
        workflow_id=raw.get("workflow_id"),
        instance=raw.get("instance"),
        aliases=[str(a) for a in aliases if a],
    )


def parse_snapshot_payload(data: Any, *, source: str = "api") -> CatalogSnapshot:
    """Normalize catalog JSON into CatalogSnapshot."""
    if not isinstance(data, dict):
        return CatalogSnapshot(source=source)

    schemes_raw = (
        data.get("schemes")
        or data.get("vector_schemes")
        or data.get("items")
        or data.get("entries")
        or []
    )
    # Allow nested { "data": { "schemes": [] } }
    if not schemes_raw and isinstance(data.get("data"), dict):
        nested = data["data"]
        schemes_raw = nested.get("schemes") or nested.get("vector_schemes") or []
        version = int(
            nested.get("catalog_version")
            or nested.get("version")
            or data.get("catalog_version")
            or data.get("version")
            or 0
        )
        updated_at = nested.get("updated_at") or data.get("updated_at")
    else:
        version = int(data.get("catalog_version") or data.get("version") or 0)
        updated_at = data.get("updated_at")

    schemes: list[CatalogScheme] = []
    if isinstance(schemes_raw, list):
        for row in schemes_raw:
            parsed = _parse_scheme_row(row)
            if parsed:
                schemes.append(parsed)

    return CatalogSnapshot(
        catalog_version=version,
        updated_at=str(updated_at) if updated_at else None,
        schemes=schemes,
        source=source,
    )


async def fetch_catalog_snapshot() -> Optional[CatalogSnapshot]:
    """Full snapshot: GET SCHEME_CATALOG_URL (snapshot endpoint preferred)."""
    base = _base_url()
    if not base:
        logger.debug("SCHEME_CATALOG_URL not set; skip remote catalog fetch")
        return None

    if base.endswith("/version"):
        url = base[: -len("/version")] + "/snapshot"
    elif base.endswith("/snapshot"):
        url = base
    else:
        # Prefer /snapshot when a base path is configured
        url = f"{base}/snapshot"

    try:
        async with httpx.AsyncClient(timeout=settings.scheme_catalog_http_timeout) as client:
            resp = await client.get(url, headers=_headers())
            resp.raise_for_status()
            return parse_snapshot_payload(resp.json(), source="api")
    except Exception as exc:
        logger.warning("Scheme catalog snapshot fetch failed from %s: %s", url, exc)
        return None
