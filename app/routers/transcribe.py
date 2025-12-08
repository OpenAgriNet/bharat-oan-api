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
        # Use the lang_code directly from request - never modify it
        transcription = transcribe_bhashini(request.audio_content, request.lang_code)
        # Always use request.lang_code in response for bhashini
        response_lang_code = request.lang_code
    elif request.service_type == 'whisper':
        detected_lang_code, transcription = transcribe_whisper(request.audio_content)
        # For whisper, use detected language or fallback to request lang_code if provided
        response_lang_code = request.lang_code if request.lang_code else detected_lang_code
    else:
        return JSONResponse({'status': 'error', 'message': 'Invalid service type'}, status_code=400)
    
    response_payload = {
        'status': 'success',
        'text': transcription,
        'lang_code': response_lang_code,  # Directly use the determined lang_code
        'session_id': session_id
    }
    
    return JSONResponse(response_payload, status_code=200)