"""Counts every POST /v1/chat/completions for the dashboard's Service Check > Voice row.

One terminal outcome per request (not per SSE chunk or model retry): success for a
2xx whose final body was sent, rejected for 4xx, error for 5xx, uncaught failures,
incomplete streams and client disconnects. Other paths, including chat, pass through.
"""
import asyncio
import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.voice import telemetry as voice_telemetry
from helpers.telemetry.voice import create_voice_completion_payload

logger = logging.getLogger(__name__)


class CompletionTelemetryMiddleware:
    def __init__(self, app: ASGIApp, path: str):
        self.app = app
        self.path = self._normalize_path(path)

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = (path or "/").strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if normalized != "/" and normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized

    def _matches_completion_path(self, scope: Scope) -> bool:
        request_path = self._normalize_path(scope.get("path") or "/")
        if request_path == self.path:
            return True

        root_path = scope.get("root_path") or ""
        if not root_path:
            return False

        full_path = self._normalize_path(f"{root_path}{scope.get('path') or '/'}")
        return full_path == self.path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not self._matches_completion_path(scope)
        ):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        timestamp = int(time.time() * 1000)
        status = 500
        completed = False
        disconnected = False
        error_type = None

        async def track_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def track_send(message: Message) -> None:
            nonlocal status, completed
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        try:
            await self.app(scope, track_receive, track_send)
        except (Exception, asyncio.CancelledError) as exc:
            error_type = (
                "client_disconnect" if isinstance(exc, asyncio.CancelledError)
                else type(exc).__name__
            )
            raise
        finally:
            if completed:
                outcome = "success" if 200 <= status < 300 else "rejected" if 400 <= status < 500 else "error"
                error_type = None if outcome == "success" else f"http_{status}"
            else:
                outcome = "error"
                error_type = "client_disconnect" if disconnected else error_type or "incomplete_response"

            payload = create_voice_completion_payload(
                url=self.path,
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000),
                outcome=outcome,
                error_type=error_type,
                timestamp=timestamp,
            )
            # Wait after the final response body, not after just the SSE headers.
            try:
                response = await asyncio.shield(voice_telemetry.send_voice_telemetry(payload))
                if response is None or not 200 <= response.status_code < 300:
                    logger.warning("Voice completion telemetry delivery failed or is not configured")
            except Exception:
                logger.exception("Voice completion telemetry delivery failed")
