"""Voice telemetry delivery, separate from chat's app.tasks.telemetry.

Uses only VOICE_TELEMETRY_* settings. Retries network errors, 429 and 5xx (as the
voice service always did); failures are logged and never change the call's response.
"""
import asyncio
import json
import logging
from typing import Any, Coroutine, Dict, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from helpers.telemetry.auth import build_telemetry_headers
from helpers.telemetry.base import TelemetryEvent, TelemetryRequest

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, read=60.0)
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)
_background: set[asyncio.Task] = set()


class _RetryableStatus(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response
        super().__init__(f"Voice telemetry returned HTTP {response.status_code}")


def _headers() -> Dict[str, str]:
    return build_telemetry_headers(
        token=settings.voice_telemetry_auth_token,
        key=settings.voice_telemetry_auth_key,
        secret=settings.voice_telemetry_auth_secret,
        origin=settings.voice_telemetry_origin,
        referer=settings.voice_telemetry_referer,
    )


async def send_voice_telemetry(payload: Dict[str, Any]) -> Optional[httpx.Response]:
    """POST a telemetry request; returns the response, or None if unset/failed."""
    url = settings.voice_telemetry_api_url
    if not url:
        logger.warning("Voice telemetry not sent: VOICE_TELEMETRY_API_URL is unset")
        return None
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=_RETRY_WAIT,
            retry=retry_if_exception_type((httpx.RequestError, _RetryableStatus)),
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    response = await client.post(url, headers=_headers(), json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    raise _RetryableStatus(response)
                return response
    except RetryError as exc:
        logger.error("Voice telemetry POST failed after retries: %s", exc.last_attempt.exception())
    return None


def voice_event_payload(event: TelemetryEvent) -> Dict[str, Any]:
    return TelemetryRequest(events=[event]).model_dump(mode="json")


async def send_voice_event(event: TelemetryEvent, *, label: str) -> None:
    """Send one voice event and log the outcome; never raises."""
    try:
        payload = voice_event_payload(event)
        logger.info("Voice telemetry %s POST body: %s", label, json.dumps(payload, ensure_ascii=False))
        response = await send_voice_telemetry(payload)
        if response is not None and 200 <= response.status_code < 300:
            logger.info("Voice telemetry %s sent", label)
        elif response is not None:
            logger.warning("Voice telemetry %s returned HTTP %s", label, response.status_code)
    except Exception:
        logger.exception("Voice telemetry %s failed", label)


def run_in_background(coro: Coroutine[Any, Any, None]) -> asyncio.Task:
    """Start telemetry delivery without delaying the call; keep a reference until done."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
