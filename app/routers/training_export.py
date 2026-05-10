"""Training-data export endpoints.

Streams chat-template JSONL built from agent traces. Intended for offline use
by an operator preparing data for SFT or DPO; not part of the runtime
user-facing surface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from helpers.training_export import (
    SCHEMA_VERSION,
    SFTRecord,
    langfuse_trace_to_sft,
    stream_jsonl,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/training", tags=["training-export"])


def _fetch_traces(
    since: Optional[datetime],
    until: Optional[datetime],
    limit: int,
) -> Iterator[dict[str, Any]]:
    """Yield trace dicts from the configured backend.

    Today this is a stub that yields zero traces — wiring it to the Langfuse
    SDK (`langfuse.api.trace.list(...)`) is intentionally left for a follow-up
    PR so this change stays reviewable. The conversion path
    (`langfuse_trace_to_sft`) is fully covered by tests against the same dict
    shape Langfuse returns, so swapping the implementation here is a one-line
    change.
    """
    logger.info(
        "training-export: trace fetch stub (since=%s until=%s limit=%s) — "
        "no Langfuse client wired yet; returning empty stream.",
        since,
        until,
        limit,
    )
    return iter(())


@router.get(
    "/export/sft",
    summary="Export agent traces as chat-template SFT JSONL",
    response_class=StreamingResponse,
)
async def export_sft(
    since: Optional[datetime] = Query(
        None, description="Start of time window (ISO 8601, UTC)."
    ),
    until: Optional[datetime] = Query(
        None, description="End of time window (ISO 8601, UTC)."
    ),
    limit: int = Query(1000, ge=1, le=10_000),
    include_tool_calls: bool = Query(
        True,
        description="Preserve assistant tool calls and tool results as "
        "intermediate messages. Disable to emit user→assistant pairs only.",
    ),
) -> StreamingResponse:
    """Stream training-ready JSONL.

    Each line is a self-contained `SFTRecord` (see `helpers.training_export`).
    The endpoint streams to keep memory bounded for large exports.
    """
    if since and until and since > until:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`since` must be earlier than `until`.",
        )

    def _records() -> Iterator[SFTRecord]:
        produced = 0
        for trace in _fetch_traces(since, until, limit):
            record = langfuse_trace_to_sft(
                trace, include_tool_calls=include_tool_calls
            )
            if record is None:
                continue
            produced += 1
            yield record
        logger.info("training-export: produced %d SFT records", produced)

    headers = {
        "X-Training-Export-Schema-Version": SCHEMA_VERSION,
        "Content-Disposition": (
            f'attachment; filename="bharat-oan-sft-'
            f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.jsonl"'
        ),
    }
    return StreamingResponse(
        stream_jsonl(_records()),
        media_type="application/x-ndjson",
        headers=headers,
    )


@router.get(
    "/export/info",
    summary="Describe the export schema and available formats",
)
async def export_info() -> dict[str, Any]:
    """Cheap introspection endpoint useful for client tooling and docs."""
    return {
        "schema_version": SCHEMA_VERSION,
        "formats": {
            "sft": {
                "endpoint": "/training/export/sft",
                "media_type": "application/x-ndjson",
                "shape": "chat-template; one SFTRecord per line",
            },
        },
        "compatibility": {
            "trl": ">=0.9 (SFTTrainer with apply_chat_template)",
        },
    }
