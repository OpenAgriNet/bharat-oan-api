from fastapi import APIRouter, Body, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from app.models.requests import TTSRequest
from helpers.tts import text_to_speech_bhashini
from typing import Any
import uuid
import base64
import time
from helpers.utils import get_logger
from app.auth.jwt_auth import get_current_user
from helpers.telemetry import create_tts_event, TelemetryRequest
from app.tasks.telemetry import send_telemetry

logger = get_logger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

@router.post("/")
async def tts(
    request: TTSRequest = Body(...), 
    current_user: dict[str, Any] = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Convert text to speech using the specified service."""
    session_id = request.session_id or str(uuid.uuid4())
    authenticated_user = current_user.get("mobile")

    logger.info(
        "TTS input | target_lang=%s session_id=%s text_length=%s",
        request.target_lang, session_id, len(request.text)
    )

    if request.service_type != 'bhashini':
        return JSONResponse({
            'status': 'error',
            'message': f'Service type "{request.service_type}" not supported. Available options: bhashini'
        }, status_code=400)

    start_time = time.time()
    success, status_code, error_code, error_message = False, 500, None, None
    audio_data = None
    uid = str(authenticated_user)if authenticated_user is not None else "system"

    try:
        audio_bytes = text_to_speech_bhashini(
            request.text, request.target_lang, gender='female', sampling_rate=8000
        )
        audio_data = base64.b64encode(audio_bytes).decode('utf-8')
        success, status_code = True, 200
    except Exception as e:
        error_code, error_message = type(e).__name__, str(e)
        logger.error(
            "TTS failed | session_id=%s target_lang=%s error=%s message=%s",
            session_id, request.target_lang, error_code, error_message[:500]
        )
        raise
    finally:
        latency_ms = (time.time() - start_time) * 1000
        try:
            telemetry_event = create_tts_event(
                success=success,
                latency_ms=latency_ms,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
                language=request.target_lang,
                session_id=session_id,
                text=request.text,
                qid=request.qid or f"tts_{session_id}",
                uid=uid
            )
            telemetry_data = TelemetryRequest(events=[telemetry_event]).model_dump()
            background_tasks.add_task(send_telemetry, telemetry_data)
        except Exception :
            logger.exception(
                "TTS telemetry event build failed | session_id=%s target_lang=%s",
                session_id, request.target_lang
            )

    logger.info(
        "TTS output | session_id=%s status=success audio_base64_len=%s latency_ms=%.2f",
        session_id, len(audio_data) if audio_data else 0, latency_ms
    )

    return JSONResponse({
        'status': 'success',
        'audio_data': audio_data,
        'session_id': session_id
    }, status_code=200)
