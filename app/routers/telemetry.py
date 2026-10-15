from fastapi import APIRouter, BackgroundTasks, Depends

from app.auth.jwt_auth import get_current_user
from app.models.requests import (
    GenericTelemetryEventRequest,
    TelemetryErrorRequest,
    TelemetryFeedbackRequest,
)
from app.services.chat_turn_map import read_chat_turn_map
from app.tasks.telemetry import send_telemetry
from helpers.langfuse_scores import create_user_feedback_score
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_error_event,
    create_chat_feedback_event,
    create_frontend_compatible_item_batch,
    create_ui_interact_event,
)
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/feedback")
async def relay_feedback_telemetry(
    request: TelemetryFeedbackRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    event = create_chat_feedback_event(
        current_user=current_user,
        qid=request.qid,
        session_id=request.session_id,
        feedback_text=request.feedback_text,
        feedback_type=request.feedback_type,
        question_text=request.question_text,
        answer_text=request.answer_text,
    )
    background_tasks.add_task(
        send_telemetry,
        TelemetryRequest(events=create_frontend_compatible_item_batch(event)).model_dump(),
    )

    # The turn map stores no user id, so the session check is the only guard
    # that this feedback belongs to the trace the qid points at.
    chat_turn = await read_chat_turn_map(request.qid)
    langfuse_score = "skipped"
    if chat_turn and chat_turn.get("trace_id"):
        stored_session_id = chat_turn.get("session_id")
        if stored_session_id and stored_session_id != request.session_id:
            logger.warning(
                "Feedback session_id %s does not match chat session_id %s for qid %s; "
                "skipping Langfuse score",
                request.session_id,
                stored_session_id,
                request.qid,
            )
        else:
            background_tasks.add_task(
                create_user_feedback_score,
                trace_id=chat_turn["trace_id"],
                qid=request.qid,
                feedback_type=request.feedback_type,
                feedback_text=request.feedback_text,
            )
            langfuse_score = "queued"

    if langfuse_score == "queued":
        logger.info("feedback_langfuse_hit qid=%s", request.qid)
    else:
        logger.info("feedback_langfuse_miss qid=%s", request.qid)

    return {"status": "accepted", "langfuse_score": langfuse_score}


@router.post("/events")
async def relay_generic_telemetry_events(
    request: list[GenericTelemetryEventRequest],
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    events = [
        create_ui_interact_event(
            current_user=current_user,
            event_name=event.event_name,
            category=event.category,
            client_time=event.time,
            metadata=event.metadata,
        )
        for event in request
    ]
    background_tasks.add_task(send_telemetry, TelemetryRequest(events=events).model_dump())
    return {"status": "accepted", "count": len(events)}


@router.post("/error")
async def relay_error_telemetry(
    request: TelemetryErrorRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    event = create_chat_error_event(
        current_user=current_user,
        qid=request.qid,
        session_id=request.session_id,
        error_text=request.error_text,
        question_text=request.question_text,
    )
    background_tasks.add_task(
        send_telemetry,
        TelemetryRequest(events=create_frontend_compatible_item_batch(event)).model_dump(),
    )
    return {"status": "accepted"}
