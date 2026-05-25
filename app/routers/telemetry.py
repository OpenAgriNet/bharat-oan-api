from fastapi import APIRouter, BackgroundTasks, Depends

from app.auth.jwt_auth import get_current_user
from app.models.requests import TelemetryErrorRequest, TelemetryFeedbackRequest
from app.tasks.telemetry import send_telemetry
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_error_event,
    create_chat_feedback_event,
)

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
    background_tasks.add_task(send_telemetry, TelemetryRequest(events=[event]).model_dump())
    return {"status": "accepted"}


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
    background_tasks.add_task(send_telemetry, TelemetryRequest(events=[event]).model_dump())
    return {"status": "accepted"}
