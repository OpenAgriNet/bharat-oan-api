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
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    """Update the active observation with optional generation fields.

    Sets Langfuse's first-class `model` field (providedModelName) when model or usage
    are provided so dashboard filters, cost, and token metrics work. Falls back to
    span update when neither is set (e.g. agent observations without LLM metadata).
    """
    meta: Optional[dict[str, Any]] = dict(metadata) if metadata else None
    has_usage = input_tokens is not None or output_tokens is not None
    usage_details: Optional[dict[str, int]] = None
    if has_usage:
        prompt_tok = int(input_tokens or 0)
        completion_tok = int(output_tokens or 0)
        usage_details = {
            "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
            "total_tokens": prompt_tok + completion_tok,
        }

    client = get_client()
    if model is not None or usage_details is not None:
        client.update_current_generation(
            input=input,
            output=output,
            metadata=meta,
            model=model,
            usage_details=usage_details,
        )
    else:
        client.update_current_span(
            input=input,
            output=output,
            metadata=meta,
        )
