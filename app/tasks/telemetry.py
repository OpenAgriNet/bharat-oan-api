import logging
from typing import Dict, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from app.config import settings
from helpers.telemetry.auth import build_telemetry_auth_token, build_telemetry_headers

logger = logging.getLogger(__name__)


def _get_telemetry_auth_token() -> Optional[str]:
    token = build_telemetry_auth_token(
        settings.telemetry_auth_token,
        settings.telemetry_auth_key,
        settings.telemetry_auth_secret,
    )
    if token is None:
        logger.warning("Telemetry auth is not configured; sending telemetry without Authorization header")
    return token


def _get_telemetry_headers() -> Dict[str, str]:
    """Chat channel headers; voice calls use app.voice.telemetry with VOICE_TELEMETRY_* settings."""
    headers = build_telemetry_headers(
        origin=settings.telemetry_origin,
        referer=settings.telemetry_referer,
    )
    auth_token = _get_telemetry_auth_token()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.RequestError)),
    reraise=True
)
async def send_telemetry(telemetry_data: Dict) -> Dict:
    """Background task to send telemetry events to the API."""
    events = telemetry_data.get("events") or []
    event_ids = [event.get("eid") for event in events if isinstance(event, dict)]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.telemetry_api_url,
            headers=_get_telemetry_headers(),
            json=telemetry_data,
            timeout=httpx.Timeout(30.0, read=60.0)
        )
        if response.is_success:
            logger.info(
                "Telemetry sent successfully - url: %s, status: %s, events: %s",
                settings.telemetry_api_url,
                response.status_code,
                event_ids,
            )
        else:
            logger.warning(
                "Telemetry send failed - url: %s, status: %s, events: %s, response: %s",
                settings.telemetry_api_url,
                response.status_code,
                event_ids,
                response.text[:500],
            )

        return {
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else response.text
        }
