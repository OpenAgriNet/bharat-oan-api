import json
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from app.config import settings
from app.core.cache import cache
from app.tasks.telemetry import send_telemetry
from helpers.telemetry import TelemetryRequest, create_ui_interact_event

router = APIRouter(tags=["agristack-callback"])
logger = logging.getLogger(__name__)

_CALLBACK_STATUS_NAMESPACE = "callback-status"
_MAX_SEARCH_DEPTH = 12


def _find_first_string(node: Any, keys: tuple[str, ...], depth: int = 0) -> Optional[str]:
    if depth > _MAX_SEARCH_DEPTH or node is None:
        return None

    if isinstance(node, str):
        stripped = node.strip()
        if not stripped.startswith("{") and not stripped.startswith("["):
            return None
        try:
            return _find_first_string(json.loads(stripped), keys, depth + 1)
        except json.JSONDecodeError:
            return None

    if isinstance(node, list):
        for item in node:
            found = _find_first_string(item, keys, depth + 1)
            if found:
                return found
        return None

    if not isinstance(node, dict):
        return None

    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)

    for value in node.values():
        found = _find_first_string(value, keys, depth + 1)
        if found:
            return found
    return None


def _extract_agristack_observability(body: Optional[Any]) -> Dict[str, Optional[str]]:
    farmer_id = _find_first_string(body, ("farmerId", "farmer_id", "frCentralId", "centralId"))
    consent_name = _find_first_string(body, ("consentName",))
    state_lgd_code = _find_first_string(body, ("stateLgdCode", "state_lgd_code", "stateCode", "state_code"))
    consent_status = "unknown"
    if consent_name:
        consent_status = "with_consent" if consent_name.upper() == "CONTINUE WITH CONSENT" else "without_consent"

    farmer_id_hash = hashlib.sha256(farmer_id.encode("utf-8")).hexdigest() if farmer_id else None
    return {
        "consent_status": consent_status,
        "state_lgd_code": state_lgd_code,
        "farmer_id_hash": farmer_id_hash,
    }


def _validate_callback_api_key(request: Request) -> None:
    expected_api_key = settings.agristack_callback_api_key
    if not expected_api_key:
        return
    received_api_key = request.headers.get("x-api-key")
    if not received_api_key or not hmac.compare_digest(received_api_key, expected_api_key):
        logger.warning(
            "callback.auth_failed from=%s callbackSessionId=%s has_x_api_key=%s x_api_key_length=%d client=%s user_agent=%s",
            request.query_params.get("from"),
            request.query_params.get("callbackSessionId"),
            bool(received_api_key),
            len(received_api_key or ""),
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid callback API key.")


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
    background_tasks: BackgroundTasks,
    source: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    """Receives AgriStack's farmer-data callback and caches it by callbackSessionId."""
    _validate_callback_api_key(request)
    body_type, body = await _extract_body(request)
    query_params = _collect_query_params(request)
    callback_session_id = _extract_callback_session_id(query_params, body)
    logger.info(
        "callback.auth_ok from=%s callbackSessionId=%s has_x_api_key=%s x_api_key_length=%d client=%s user_agent=%s",
        source,
        callback_session_id,
        bool(request.headers.get("x-api-key")),
        len(request.headers.get("x-api-key") or ""),
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )
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

    try:
        observability = _extract_agristack_observability(body)
        common_metadata = {
            "sid": callback_session_id,
            "source": source,
            "callback_status": "received",
            "consent_status": observability["consent_status"],
            "state_lgd_code": observability["state_lgd_code"],
            "farmer_id_hash": observability["farmer_id_hash"],
        }
        connect_event = create_ui_interact_event(
            current_user={"channel": "BharatVistaar"},
            event_name="agristack_connect_click",
            category="agristack",
            client_time=response["received_at_utc"],
            metadata={**common_metadata, "origin": "api_callback"},
        )
        redirect_event = create_ui_interact_event(
            current_user={"channel": "BharatVistaar"},
            event_name="agristack_redirect_success",
            category="agristack",
            client_time=response["received_at_utc"],
            metadata={**common_metadata, "origin": "api_callback"},
        )
        background_tasks.add_task(
            send_telemetry, TelemetryRequest(events=[connect_event, redirect_event]).model_dump()
        )
    except Exception:
        logger.exception("callback.telemetry_emit_failed callbackSessionId=%s", callback_session_id)

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
