import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.auth.jwt_auth import get_current_user
from app.models.requests import ChatRequest
from app.routers.chat_query_utils import (
    get_session_history,
    normalize_chat_query,
    prepare_image_analyze_payload,
)
from app.services.chat import stream_chat_messages
from helpers.utils import get_logger


logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/")
async def chat_endpoint(
    background_tasks: BackgroundTasks,
    request: ChatRequest = Depends(),
    current_user: dict[str, Any] = Depends(get_current_user),  # Authentication required
):
    """
    Chat endpoint that streams responses back to the client.
    Requires JWT authentication.
    """
    session_id, history = await get_session_history(request.session_id)
    qid = request.qid or str(uuid.uuid4())
    channel = current_user.get("channel", "BharatVistaar")
    authenticated_user = current_user.get("mobile")

    logger.info(
        f"Chat request received - session_id: {session_id}, qid: {qid}, user_id: {request.user_id}, "
        f"authenticated_user: {authenticated_user}, channel: {channel}, source_lang: {request.source_lang}, "
        f"target_lang: {request.target_lang}, query: {request.query}"
    )
    logger.debug(f"Retrieved message history for session {session_id} - length: {len(history)}")

    query_text, is_image_analysis = normalize_chat_query(request.query or "")

    return StreamingResponse(
        stream_chat_messages(
            query=query_text,
            session_id=session_id,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            user_id=request.user_id,
            history=history,
            background_tasks=background_tasks,
            channel=channel,
            is_image_analysis=is_image_analysis,
            latitude=request.latitude,
            longitude=request.longitude,
            qid=qid,
            current_user=current_user,
        ),
        media_type="text/event-stream",
        background=background_tasks,
    )


@router.post("/analyze-image")
async def chat_analyze_image_endpoint(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(..., description="Crop image to analyze (JPEG or PNG)"),
    query: str = Form("Analyze this crop image", description="User query accompanying the image"),
    session_id: Optional[str] = Form(None, description="Session ID for maintaining conversation context"),
    source_lang: str = Form("hi", description="Source language code"),
    target_lang: str = Form("hi", description="Target language code"),
    user_id: str = Form("anonymous", description="User identifier"),
    latitude: Optional[float] = Form(None, description="User latitude for geocode lookup"),
    longitude: Optional[float] = Form(None, description="User longitude for geocode lookup"),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    DEPRECATED: Use POST /api/image/upload first, then send only the returned image_id
    in a regular chat message.

    Convenience endpoint that accepts an image upload, saves it to temp storage,
    and immediately streams a chat response analyzing it.
    """
    channel = current_user.get("channel", "BharatVistaar")
    authenticated_user = current_user.get("mobile")

    logger.info(
        f"Image analyze request received - session_id: {session_id}, user_id: {user_id}, "
        f"authenticated_user: {authenticated_user}, channel: {channel}, source_lang: {source_lang}, "
        f"target_lang: {target_lang}, query: {query}"
    )

    session_id, user_message, history = await prepare_image_analyze_payload(
        image=image,
        query=query,
        session_id=session_id,
    )
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
            channel=channel,
            is_image_analysis=True,
            latitude=latitude,
            longitude=longitude,
            qid=str(uuid.uuid4()),
            current_user=current_user,
        ),
        media_type="text/event-stream",
        background=background_tasks,
    )
