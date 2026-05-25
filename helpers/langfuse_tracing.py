"""Langfuse SDK helpers aligned with OpenTelemetry-based client (langfuse ≥3.x)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from helpers import langfuse_helper  # noqa: F401 — initializes Langfuse env before get_client()
from langfuse import get_client


def lf_update_current_span(
    *,
    input: Any = None,
    output: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Set input/output on the active observation (replaces deprecated trace-level I/O)."""
    get_client().update_current_span(
        input=input,
        output=output,
        metadata=dict(metadata) if metadata else None,
    )


def lf_update_current_observation(
    *,
    input: Any = None,
    output: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
    model: Optional[str] = None,
    request_tokens: Optional[int] = None,
    response_tokens: Optional[int] = None,
) -> None:
    """Update the active generation observation (not trace tags).

    Sets Langfuse's first-class `model` field (providedModelName) so model filters,
    cost tracking, and dashboard widgets work. Call inside @observe(as_type="generation").
    """
    meta: Optional[dict[str, Any]] = dict(metadata) if metadata else None
    usage_details: Optional[dict[str, int]] = None
    if request_tokens is not None or response_tokens is not None:
        prompt_tok = int(request_tokens or 0)
        completion_tok = int(response_tokens or 0)
        usage_details = {
            "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
            "total_tokens": prompt_tok + completion_tok,
        }

    get_client().update_current_generation(
        input=input,
        output=output,
        metadata=meta,
        model=model,
        usage_details=usage_details,
    )
