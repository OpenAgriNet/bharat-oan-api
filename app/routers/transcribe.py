import uuid
import time
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from app.models.requests import TranscribeRequest
from helpers.transcription import transcribe_bhashini, transcribe_whisper
from app.auth.jwt_auth import get_current_user

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

@router.post("/")
async def transcribe(request: TranscribeRequest = Body(...), current_user: str = Depends(get_current_user)):
    """
    Transcribe audio content using the specified service.
    """
    session_id = request.session_id or str(uuid.uuid4())
    
    if request.service_type == 'bhashini':
        lang_code = request.lang_code
        transcription = transcribe_bhashini(request.audio_content, lang_code)
        # Override lang_code in response to match the requested language
        lang_code = request.lang_code  # Force the response to use the requested language code
    elif request.service_type == 'whisper':
        lang_code, transcription = transcribe_whisper(request.audio_content)
    else:
        return JSONResponse({'status': 'error', 'message': 'Invalid service type'}, status_code=400)
    
    response_payload = {
        'status': 'success',
        'text': transcription,
        'lang_code': lang_code,  
        'session_id': session_id
    }
    
    return JSONResponse(response_payload, status_code=200)