import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Query, Request, status
from app.config import settings
from app.core.cache import cache

router = APIRouter(tags=["agristack-callback"])
logger = logging.getLogger(__name__)

_CALLBACK_STATUS_NAMESPACE = "callback-status"


def _extract_callback_session_id(query_params: Dict[str, Any], body: Optional[Any]) -> Optional[str]:
    # Prefer explicit callbackSessionId query param when provided.
    query_session_id = query_params.get("callbackSessionId")
    if isinstance(query_session_id, str) and query_session_id.strip():
        return query_session_id.strip()

    if not isinstance(body, dict):
        return None

    direct_keys = ["callbackSessionId", "sessionId"]
    for key in direct_keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    data_section = body.get("data")
    if isinstance(data_section, dict):
        nested_value = data_section.get("sessionId")
        if isinstance(nested_value, str) and nested_value.strip():
            return nested_value.strip()

    return None


async def _mark_callback_received(
    callback_session_id: str,
    source: Optional[str],
    method: str,
    body: Optional[Any],
) -> None:
    callback_state = {
        "status": "received",
        "callbackSessionId": callback_session_id,
        "source": source,
        "method": method,
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "body": body,
    }
    await cache.set(
        callback_session_id,
        callback_state,
        ttl=settings.callback_session_ttl_seconds,
        namespace=_CALLBACK_STATUS_NAMESPACE,
    )


def _collect_query_params(request: Request) -> Dict[str, Any]:
    query_params: Dict[str, Any] = {}
    for key, value in request.query_params.multi_items():
        if key in query_params:
            existing = query_params[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                query_params[key] = [existing, value]
        else:
            query_params[key] = value
    return query_params


def _collect_headers(request: Request) -> Dict[str, str]:
    important_headers = {"content-type", "user-agent", "referer", "origin", "x-forwarded-for", "x-real-ip"}
    selected: Dict[str, str] = {}

    for key, value in request.headers.items():
        key_lower = key.lower()
        if key_lower in important_headers or key_lower.startswith("x-"):
            selected[key] = value

    return selected


async def _extract_body(request: Request) -> Tuple[Optional[str], Optional[Any]]:
    raw_body = await request.body()
    if not raw_body:
        return None, None

    content_type = request.headers.get("content-type", "").lower()

    if "application/json" in content_type:
        try:
            return "json", json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "raw", raw_body.decode("utf-8", errors="replace")

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            body: Dict[str, Any] = {}
            for key in form.keys():
                values = form.getlist(key)
                body[key] = values if len(values) > 1 else values[0]
            return "form", body
        except Exception:
            return "raw", raw_body.decode("utf-8", errors="replace")

    return "raw", raw_body.decode("utf-8", errors="replace")


@router.post("/callback", status_code=status.HTTP_200_OK)
async def agristack_callback(
    request: Request,
    source: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    """Receives AgriStack's farmer-data callback and caches it by callbackSessionId."""
    body_type, body = await _extract_body(request)
    query_params = _collect_query_params(request)
    callback_session_id = _extract_callback_session_id(query_params, body)
    response = {
        "status": "received",
        "from": source,
        "source": source,
        "callbackSessionId": callback_session_id,
        "method": request.method,
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "query_params": query_params,
        "headers": _collect_headers(request),
        "body_type": body_type,
        "body": body,
    }

    if callback_session_id:
        try:
            await _mark_callback_received(callback_session_id, source, request.method, body)
        except Exception as exc:
            logger.exception("callback.status_store_failed callbackSessionId=%s error=%s", callback_session_id, str(exc))

    logger.info("callback.received %s", json.dumps(response, default=str))
    return response


@router.get("/callback/status", status_code=status.HTTP_200_OK)
async def agristack_callback_status(
    callback_session_id: str = Query(..., alias="callbackSessionId"),
    source: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    """Returns callback receipt status (and cached body) for a callbackSessionId."""
    callback_state = await cache.get(callback_session_id, namespace=_CALLBACK_STATUS_NAMESPACE)
    if not callback_state:
        response = {
            "callbackSessionId": callback_session_id,
            "from": source,
            "status": "not_found",
        }
        logger.info("callback.status_check %s", json.dumps(response, default=str))
        return response

    response = {
        "callbackSessionId": callback_session_id,
        "from": source,
        "status": callback_state.get("status", "received"),
        "received_at_utc": callback_state.get("received_at_utc"),
        "source": callback_state.get("source"),
        "method": callback_state.get("method"),
        "body": callback_state.get("body"),
    }
    logger.info("callback.status_check %s", json.dumps(response, default=str))
    return response
