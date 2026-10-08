"""Set Langfuse client env before get_client() / OpenTelemetry export."""

import os

from app.config import settings
from langfuse import Langfuse


def get_langfuse_tracing_environment() -> str:
    """Value for LANGFUSE_TRACING_ENVIRONMENT / trace env tags. Works if Settings has no langfuse_tracing_environment (older config)."""
    configured = getattr(settings, "langfuse_tracing_environment", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return (
        os.getenv("LANGFUSE_TRACING_ENVIRONMENT")
        or os.getenv("ENVIRONMENT", "production")
    )


os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key or ""
os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
os.environ["LANGFUSE_HOST"] = (
    settings.langfuse_host or os.getenv("LANGFUSE_BASE_URL") or ""
)
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = get_langfuse_tracing_environment()

langfuse = Langfuse()
