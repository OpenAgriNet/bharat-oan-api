from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from app.models.requests import TTSRequest
from helpers.tts import text_to_speech_bhashini, text_to_speech_elevenlabs, remove_urls
import uuid
import base64
from helpers.utils import get_logger
from app.auth.jwt_auth import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

@router.post("/")
async def tts(request: TTSRequest = Body(...), current_user: str = Depends(get_current_user)):
    """
    Convert text to speech using the specified service.
    """
    session_id = request.session_id or str(uuid.uuid4())
    logger.info(f"TTS request: {request}")
    
    # Support both bhashini and eleven_labs services
    if request.service_type == 'bhashini':
        audio_data = text_to_speech_bhashini(
            request.text, 
            request.target_lang, 
            gender='female', 
            sampling_rate=8000
        )
        
    # elif request.service_type == 'eleven_labs':
    #     text = remove_urls(request.text)
    #     audio_data = text_to_speech_elevenlabs(text=text)
    else:
        return JSONResponse({
            'status': 'error',
            'message': f'Service type "{request.service_type}" not supported. Available options: bhashini, eleven_labs'
        }, status_code=400)
    
    audio_data = base64.b64encode(audio_data).decode('utf-8')
        
    return JSONResponse({
        'status': 'success',
        'audio_data': audio_data,
        'session_id': session_id
    }, status_code=200)