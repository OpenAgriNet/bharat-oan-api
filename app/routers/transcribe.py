import uuid
import time
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from app.models.requests import TranscribeRequest
from helpers.transcription import transcribe_bhashini, transcribe_whisper
from helpers.utils import get_logger
from app.auth.jwt_auth import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

@router.post("/")
async def transcribe(request: TranscribeRequest = Body(...), current_user: str = Depends(get_current_user)):
    """
    Transcribe audio content using the specified service.
    """
    logger.info(f"[TRANSCRIBE START] Request received - lang_code: '{request.lang_code}', service_type: '{request.service_type}'")
    
    session_id = request.session_id or str(uuid.uuid4())
    
    current_timestamp = int(time.time() * 1000)
    
    if request.service_type == 'bhashini':
        lang_code = request.lang_code
        logger.info(f"[TRANSCRIBE] Before transcribe_bhashini call - lang_code variable: '{lang_code}', request.lang_code: '{request.lang_code}'")
        transcription = transcribe_bhashini(request.audio_content, lang_code)
        logger.info(f"[TRANSCRIBE] After transcribe_bhashini - lang_code: '{lang_code}', transcription length: {len(transcription) if transcription else 0}, transcription: '{transcription[:100] if transcription else 'None'}'")
    elif request.service_type == 'whisper':
        lang_code, transcription = transcribe_whisper(request.audio_content)
    else:
        return JSONResponse({
            'status': 'error',
            'message': 'Invalid service type'
        }, status_code=400)
        
    response_payload = {
        'status': 'success',
        'text': transcription,
        'lang_code': lang_code,
        'session_id': session_id
    }
    logger.info(f"[TRANSCRIBE END] About to return - lang_code in response: '{lang_code}', text length: {len(transcription) if transcription else 0}, response_payload: {response_payload}")
    
    return JSONResponse(response_payload, status_code=200)