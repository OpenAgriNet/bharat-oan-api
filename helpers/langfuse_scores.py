"""Attach post-hoc user feedback as a score on an existing Langfuse trace.

Scores are the supported way to enrich a trace after it has been ingested, so
feedback (which always arrives after the chat turn is done) is written as a
score on the original chat trace rather than as a new trace.
"""

from __future__ import annotations

from langfuse import get_client

from helpers.utils import get_logger

logger = get_logger(__name__)

FEEDBACK_SCORE_NAME = "user-feedback"


def create_user_feedback_score(
    *,
    trace_id: str,
    qid: str,
    feedback_type: str,
    feedback_text: str = "",
) -> None:
    """Idempotent upsert: repeated feedback for the same qid overwrites the prior score."""
    try:
        get_client().create_score(
            name=FEEDBACK_SCORE_NAME,
            value=feedback_type,
            data_type="CATEGORICAL",
            comment=feedback_text or None,
            trace_id=trace_id,
            score_id=f"{FEEDBACK_SCORE_NAME}:{qid}",
        )
    except Exception:
        logger.exception("Failed to write Langfuse user-feedback score for qid %s", qid)
