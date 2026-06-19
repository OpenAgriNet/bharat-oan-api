import uuid
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from app.models.requests import TranscribeRequest
from helpers.transcription import transcribe_bhashini, transcribe_whisper
from app.auth.jwt_auth import get_current_user
from helpers.telemetry import create_asr_event, TelemetryRequest
from app.tasks.telemetry import send_telemetry
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

@router.post("/")
async def transcribe(
    request: TranscribeRequest = Body(...), 
    current_user: dict[str, Any] = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Transcribe audio content using the specified service."""
    session_id = request.session_id or str(uuid.uuid4())
    authenticated_user = current_user.get("mobile") or "system"

    logger.info(
        "Transcribe input | service_type=%s lang_code=%s session_id=%s audio_content_len=%s",
        request.service_type, request.lang_code, session_id, len(request.audio_content)
    )

    if request.service_type not in ['bhashini', 'whisper']:
        return JSONResponse({
            'status': 'error',
            'message': 'Invalid service type'
        }, status_code=400)

    start_time = time.time()
    success, status_code, error_code, error_message = False, 500, None, None
    transcription, response_lang_code = None, None
    uid = str(authenticated_user) if authenticated_user is not None else "system"

    try:
        if request.service_type == 'bhashini':
            transcription = transcribe_bhashini(request.audio_content, request.lang_code)
            response_lang_code = request.lang_code
        else:
            response_lang_code, transcription = transcribe_whisper(request.audio_content)
        success, status_code = True, 200
    except Exception as e:
        error_code, error_message = type(e).__name__, str(e)
        logger.error(
            "Transcribe failed | session_id=%s service_type=%s error=%s message=%s",
            session_id, request.service_type, error_code, error_message[:500]
        )
        raise
    finally:
        latency_ms = (time.time() - start_time) * 1000
        try:
            telemetry_event = create_asr_event(
                success=success,
                latency_ms=latency_ms,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
                language=request.lang_code if request.service_type == 'bhashini' else None,
                session_id=session_id,
                text=transcription,
                qid=request.qid or f"asr_{session_id}",
                uid=uid
            )
            telemetry_data = TelemetryRequest(events=[telemetry_event]).model_dump()
            background_tasks.add_task(send_telemetry, telemetry_data)
        except Exception:
            logger.exception(
                "Transcribe telemetry event build failed | session_id=%s service_type=%s",
                session_id, request.service_type
            )

    logger.info(
        "Transcribe output | session_id=%s status=success result_length=%s latency_ms=%.2f",
        session_id, len(transcription) if transcription else 0, latency_ms
    )

    return JSONResponse({
        'status': 'success',
        'text': transcription,
        'lang_code': response_lang_code,
        'session_id': session_id
    }, status_code=200)
