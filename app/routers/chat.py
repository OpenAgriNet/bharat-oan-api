from typing import Optional
from fastapi import APIRouter, Depends, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from app.auth.jwt_auth import get_current_user
from app.services.chat import stream_chat_messages
from app.utils import _get_message_history
from app.models.requests import ChatRequest
from app.core.image_storage import save_uploaded_image
from helpers.utils import get_logger
import uuid

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

@router.get("/")
async def chat_endpoint(
    background_tasks: BackgroundTasks,
    request: ChatRequest = Depends(),
    current_user: str = Depends(get_current_user)  # Authentication required
):
    """
    Chat endpoint that streams responses back to the client.
    Requires JWT authentication.
    """
    session_id = request.session_id or str(uuid.uuid4())
    
    logger.info(
        f"Chat request received - session_id: {session_id}, user_id: {request.user_id}, "
        f"authenticated_user: {current_user}, source_lang: {request.source_lang}, "
        f"target_lang: {request.target_lang}, query: {request.query}"
    )
    
    history = await _get_message_history(session_id)
    logger.debug(f"Retrieved message history for session {session_id} - length: {len(history)}")
    
    # Detect if query contains an image URL and set flag accordingly
    query_text = request.query or ""
    has_image_url = "/api/image/" in query_text
    
    return StreamingResponse(
        stream_chat_messages(
            query=query_text,
            session_id=session_id,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            user_id=request.user_id,
            history=history,
            background_tasks=background_tasks,
            is_image_analysis=has_image_url,
            latitude=request.latitude,
            longitude=request.longitude,
        ),
        media_type='text/event-stream'
    )


@router.post("/analyze-image")
async def chat_analyze_image_endpoint(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(..., description="Crop image to analyze (JPEG or PNG)"),
    query: str = Form("Analyze this crop image", description="User query accompanying the image"),
    session_id: Optional[str] = Form(None, description="Session ID for maintaining conversation context"),
    source_lang: str = Form('hi', description="Source language code"),
    target_lang: str = Form('hi', description="Target language code"),
    user_id: str = Form('anonymous', description="User identifier"),
    latitude: Optional[float] = Form(None, description="User latitude for geocode lookup"),
    longitude: Optional[float] = Form(None, description="User longitude for geocode lookup"),
    current_user: str = Depends(get_current_user)
):
    """
    DEPRECATED: Use POST /api/image/upload first, then include the returned image_url
    in a regular chat message.

    Convenience endpoint that accepts an image upload, saves it to temp storage,
    and immediately streams a chat response analyzing it.
    """
    session_id = session_id or str(uuid.uuid4())
    
    logger.info(
        f"Image analyze request received - session_id: {session_id}, user_id: {user_id}, "
        f"authenticated_user: {current_user}, source_lang: {source_lang}, "
        f"target_lang: {target_lang}, query: {query}"
    )

    # Save image to temp storage
    image_id, image_url = await save_uploaded_image(image)

    # Build user message with embedded image URL so the agent sees only the URL
    user_message = (
        f"{query.strip() or 'Analyze this crop image for pests or diseases.'}\n\n"
        f"[IMAGE_URL: {image_url}]"
    )

    history = await _get_message_history(session_id)
    logger.debug(f"Retrieved message history for session {session_id} - length: {len(history)}")

    return StreamingResponse(
        stream_chat_messages(
            query=user_message,
            session_id=session_id,
            source_lang=source_lang,
            target_lang=target_lang,
            user_id=user_id,
            history=history,
            background_tasks=background_tasks,
            is_image_analysis=True,
            image_url=image_url,
            latitude=latitude,
            longitude=longitude,
        ),
        media_type='text/event-stream'
    )
