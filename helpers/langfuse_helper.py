"""Langfuse setup: chat and voice trace to different projects in the same process.

- Chat uses LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY on the global OpenTelemetry
  tracer provider, exactly as before.
- Voice uses VOICE_LANGFUSE_PUBLIC_KEY / VOICE_LANGFUSE_SECRET_KEY on the same
  LANGFUSE_HOST, with its own tracer provider, so PydanticAI spans from calls never
  reach the chat project (and chat spans never reach voice).
- LangfuseProjectMiddleware marks each request with its project. Inside a request,
  every un-keyed get_client() / @observe call then resolves to that project; without
  this, Langfuse disables tracing as soon as two projects exist.

Without VOICE_LANGFUSE_* keys there is one project and voice traces go to chat's.
"""

import logging
import os
from contextlib import nullcontext
from typing import Optional

from app.config import settings
from langfuse import Langfuse

logger = logging.getLogger(__name__)


def get_langfuse_tracing_environment() -> str:
    """Value for LANGFUSE_TRACING_ENVIRONMENT / trace env tags. Works if Settings has no langfuse_tracing_environment (older config)."""
    configured = getattr(settings, "langfuse_tracing_environment", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return (
        os.getenv("LANGFUSE_TRACING_ENVIRONMENT")
        or os.getenv("ENVIRONMENT", "production")
    )


LANGFUSE_HOST = settings.langfuse_host or os.getenv("LANGFUSE_BASE_URL") or ""
CHAT_PUBLIC_KEY = settings.langfuse_public_key or ""

os.environ["LANGFUSE_PUBLIC_KEY"] = CHAT_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = get_langfuse_tracing_environment()
# Long voice conversations repeat prompt/history content across spans; compress
# batches so the ingress does not reject them with HTTP 413.
os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_COMPRESSION", "gzip")

langfuse = Langfuse()


def _voice_project():
    """(public key, client, tracer provider) for voice, or Nones when not configured."""
    public_key = settings.voice_langfuse_public_key
    secret_key = settings.voice_langfuse_secret_key
    if not (public_key and secret_key) or public_key == CHAT_PUBLIC_KEY:
        return None, None, None
    try:
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        client = Langfuse(public_key=public_key, secret_key=secret_key, host=LANGFUSE_HOST,
                          tracer_provider=provider)
        return public_key, client, provider
    except Exception:
        logger.exception("Voice Langfuse project could not be set up; voice traces go to the chat project")
        return None, None, None


VOICE_PUBLIC_KEY, voice_langfuse, voice_tracer_provider = _voice_project()


def separate_voice_project() -> bool:
    return VOICE_PUBLIC_KEY is not None


def project_public_key(channel: str) -> Optional[str]:
    """Langfuse public key for "chat" or "voice" (voice falls back to chat)."""
    if channel == "voice" and VOICE_PUBLIC_KEY:
        return VOICE_PUBLIC_KEY
    return CHAT_PUBLIC_KEY or None


def use_project(channel: str):
    """Context manager: un-keyed Langfuse calls inside resolve to this channel's project."""
    if not separate_voice_project():
        return nullcontext()  # One project: Langfuse needs no key.
    from langfuse._client.get_client import _set_current_public_key  # pinned langfuse==4.14.1

    return _set_current_public_key(project_public_key(channel))


def flush_all() -> None:
    for client in (langfuse, voice_langfuse):
        if client is None:
            continue
        try:
            client.flush()
        except Exception:
            logger.warning("Langfuse flush failed", exc_info=True)


class LangfuseProjectMiddleware:
    """Run each HTTP request under its channel's Langfuse project (voice path -> voice)."""

    def __init__(self, app, voice_path: str):
        self.app = app
        self.voice_path = voice_path.rstrip("/")

    def channel_for(self, scope) -> str:
        path = (scope.get("root_path") or "") + (scope.get("path") or "")
        candidates = {path.rstrip("/"), (scope.get("path") or "").rstrip("/")}
        return "voice" if self.voice_path in candidates else "chat"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with use_project(self.channel_for(scope)):
            await self.app(scope, receive, send)
