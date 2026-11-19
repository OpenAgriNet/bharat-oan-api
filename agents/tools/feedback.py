"""End-of-call feedback (like/dislike plus improvement text) for voice calls only.

Not part of the shared skill catalogue: chat has its own feedback endpoint. The voice
agent registers this tool next to the shared TOOLS (agents/voice.py).
"""
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext, Tool

from agents.deps import FarmerContext
from app.config import settings
from app.voice import telemetry as voice_telemetry
from helpers.telemetry.voice import create_feedback_event

logger = logging.getLogger(__name__)


class FeedbackInput(BaseModel):
    """Input for submitting call feedback. Call this tool only after the user has given a feedback type (and optionally what went wrong or how to improve)."""

    feedback_type: Literal["like", "dislike"] = Field(
        ...,
        description="The type of feedback: 'like' for positive feedback, 'dislike' for negative feedback. Set only when the user has stated their feedback.",
    )
    feedback_text: Optional[str] = Field(
        default=None,
        description="What went wrong or how we can improve the experience, in the user's words. Leave empty if the user did not provide this or said nothing/skip.",
    )


# async: sync tools run in a worker thread, where the background telemetry task
# below could not be scheduled on the event loop.
async def submit_feedback(ctx: RunContext[FarmerContext], feedback: FeedbackInput) -> str:
    """
    Submit call feedback before ending the interaction. Use this tool ONLY when:
    (1) The farmer has indicated they want to end the call, and
    (2) You have asked for a feedback type and the farmer has given a feedback type, and
    (3) You have asked what went wrong or how to improve (the farmer may skip or say nothing).
    Call this tool with the feedback type and any improvement text the user provided, then thank them for the feedback and give your closing response.
    """
    if not ctx.deps.feedback_prompt_seen:
        logger.warning(
            "Rejected premature feedback submit: session_id=%s user_id=%s",
            ctx.deps.session_id,
            ctx.deps.user_id or "guest",
        )
        raise ModelRetry(
            "Do not submit feedback yet. First ask the end-of-call feedback question in the "
            "farmer's language (helpful yes/no and brief reason). Then wait for the farmer's "
            "feedback answer, and only then call submit_feedback."
        )

    ctx.deps.end_interaction = True
    session_id = ctx.deps.session_id
    user_id = ctx.deps.user_id or "guest"
    logger.info(
        "Voice feedback: session_id=%s, user_id=%s, feedback_type=%s, feedback_text=%s",
        session_id,
        user_id,
        feedback.feedback_type,
        feedback.feedback_text or "(none)",
    )
    try:
        event = create_feedback_event(
            session_id=session_id,
            feedback_type=feedback.feedback_type,
            feedback_text=(feedback.feedback_text or f"Feedback type: {feedback.feedback_type}").strip(),
            uid=user_id,
            channel=settings.voice_telemetry_channel,
        )
        # Sent in the background so the closing reply is not held up by telemetry retries.
        voice_telemetry.run_in_background(voice_telemetry.send_voice_event(event, label="TeleFeedback"))
    except Exception:
        logger.exception("Failed to queue feedback telemetry")

    return "Feedback recorded. Thank you for your feedback. Thank you for calling the Bharat VISTAAR helpline. Have a good day."


FEEDBACK_TOOL = Tool(submit_feedback, takes_ctx=True, strict=False)
