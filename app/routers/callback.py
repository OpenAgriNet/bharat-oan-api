import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Query, Request, status

router = APIRouter(tags=["agristack-callback"])
logger = logging.getLogger(__name__)

_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


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


async def _build_callback_response(request: Request, source: Optional[str], wildcard_path: str) -> Dict[str, Any]:
    body_type, body = await _extract_body(request)
    response = {
        "status": "received",
        "from": source,
        "source": source,
        "method": request.method,
        "callback_path": f"/{wildcard_path}" if wildcard_path else "",
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "query_params": _collect_query_params(request),
        "headers": _collect_headers(request),
        "body_type": body_type,
        "body": body,
    }

    logger.info("callback.received %s", json.dumps(response, default=str))
    return response


@router.api_route("/callback", methods=_ALLOWED_METHODS, status_code=status.HTTP_200_OK)
async def agristack_callback(
    request: Request,
    source: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    """Universal callback endpoint for AgriStack redirects and server callbacks."""
    return await _build_callback_response(request, source, "")


@router.api_route("/callback/{wildcard_path:path}", methods=_ALLOWED_METHODS, status_code=status.HTTP_200_OK)
async def agristack_callback_wildcard(
    request: Request,
    wildcard_path: str,
    source: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    """Wildcard variant that accepts callbacks on nested subpaths."""
    return await _build_callback_response(request, source, wildcard_path)
