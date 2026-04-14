"""Langfuse SDK helpers aligned with OpenTelemetry-based client (langfuse ≥3.x)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from helpers import langfuse_helper  # noqa: F401 — initializes Langfuse env before get_client()
from langfuse import get_client


def lf_set_trace_io(*, input: Any = None, output: Any = None) -> None:
    """Trace-level input/output (legacy API; still used by Langfuse UI for top-level I/O)."""
    get_client().set_current_trace_io(input=input, output=output)


def lf_update_current_observation(
    *,
    input: Any = None,
    output: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
    model: Optional[str] = None,
    request_tokens: Optional[int] = None,
    response_tokens: Optional[int] = None,
) -> None:
    """Update the active observation (agent/span/tool). Token usage is stored on metadata for non-generation types."""
    meta: dict[str, Any] = dict(metadata) if metadata else {}
    if model is not None:
        meta["model"] = model
    if request_tokens is not None:
        meta["usage_request_tokens"] = request_tokens
    if response_tokens is not None:
        meta["usage_response_tokens"] = response_tokens
    get_client().update_current_span(
        input=input,
        output=output,
        metadata=meta or None,
    )
