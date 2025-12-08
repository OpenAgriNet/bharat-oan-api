import uuid
import time
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from app.models.requests import TranscribeRequest
from helpers.transcription import transcribe_bhashini, transcribe_whisper
from app.auth.jwt_auth import get_current_user
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

@router.post("/")
async def transcribe(request: TranscribeRequest = Body(...), current_user: str = Depends(get_current_user)):
    """
    Transcribe audio content using the specified service.
    """
    # Log incoming request details (both logger and print for visibility)
    log_msg = f"[TRANSCRIBE REQUEST] lang_code from request: '{request.lang_code}', service_type: '{request.service_type}', session_id: '{request.session_id}'"
    logger.info(log_msg)
    print(log_msg)  # Print to stdout for guaranteed visibility
    
    log_msg2 = f"[TRANSCRIBE REQUEST] Full request object - lang_code: '{request.lang_code}', type: {type(request.lang_code)}"
    logger.info(log_msg2)
    print(log_msg2)
    
    session_id = request.session_id or str(uuid.uuid4())
    
    if request.service_type == 'bhashini':
        log_msg = f"[TRANSCRIBE BHASHINI] About to call transcribe_bhashini with lang_code: '{request.lang_code}'"
        logger.info(log_msg)
        print(log_msg)
        # Use the lang_code directly from request - never modify it
        transcription = transcribe_bhashini(request.audio_content, request.lang_code)
        log_msg = f"[TRANSCRIBE BHASHINI] Transcription received - length: {len(transcription) if transcription else 0}, preview: '{transcription[:50] if transcription else 'None'}...'"
        logger.info(log_msg)
        print(log_msg)
        # Always use request.lang_code in response for bhashini
        response_lang_code = request.lang_code
        log_msg = f"[TRANSCRIBE BHASHINI] response_lang_code set to: '{response_lang_code}' (from request.lang_code: '{request.lang_code}')"
        logger.info(log_msg)
        print(log_msg)
    elif request.service_type == 'whisper':
        logger.info(f"[TRANSCRIBE WHISPER] Calling transcribe_whisper")
        detected_lang_code, transcription = transcribe_whisper(request.audio_content)
        logger.info(f"[TRANSCRIBE WHISPER] Detected lang_code: '{detected_lang_code}', request.lang_code: '{request.lang_code}'")
        # For whisper, use detected language or fallback to request lang_code if provided
        response_lang_code = request.lang_code if request.lang_code else detected_lang_code
        logger.info(f"[TRANSCRIBE WHISPER] response_lang_code set to: '{response_lang_code}'")
    else:
        logger.error(f"[TRANSCRIBE ERROR] Invalid service_type: '{request.service_type}'")
        return JSONResponse({'status': 'error', 'message': 'Invalid service type'}, status_code=400)
    
    response_payload = {
        'status': 'success',
        'text': transcription,
        'lang_code': response_lang_code,  # Directly use the determined lang_code
        'session_id': session_id
    }
    
    log_msg = f"[TRANSCRIBE RESPONSE] About to return - response_lang_code: '{response_lang_code}', text_length: {len(transcription) if transcription else 0}"
    logger.info(log_msg)
    print(log_msg)
    
    log_msg2 = f"[TRANSCRIBE RESPONSE] Full response_payload: {response_payload}"
    logger.info(log_msg2)
    print(log_msg2)
    
    return JSONResponse(response_payload, status_code=200)