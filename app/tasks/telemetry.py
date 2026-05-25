import base64
import hashlib
import hmac
import json
import logging
from typing import Dict

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from app.config import settings

logger = logging.getLogger(__name__)


def _get_telemetry_auth_token() -> str:
    if settings.telemetry_auth_token:
        return settings.telemetry_auth_token

    header = {"typ": "JWT", "alg": "HS256"}
    payload = {
        "iss": settings.telemetry_auth_key,
        "iat": None,
        "exp": None,
        "aud": "",
        "sub": "",
    }

    def base64_url_encode(value: Dict) -> str:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()

    unsigned_token = f"{base64_url_encode(header)}.{base64_url_encode(payload)}"
    signature = hmac.new(
        settings.telemetry_auth_secret.encode(),
        unsigned_token.encode(),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    return f"{unsigned_token}.{encoded_signature}"


def _get_telemetry_headers() -> Dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "dataType": "json",
        "Authorization": f"Bearer {_get_telemetry_auth_token()}",
    }

    if settings.telemetry_origin:
        headers["Origin"] = settings.telemetry_origin
    if settings.telemetry_referer:
        headers["Referer"] = settings.telemetry_referer

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
