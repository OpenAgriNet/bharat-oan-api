from fastapi import APIRouter, Depends, BackgroundTasks
from app.auth.jwt_auth import get_current_user
from app.services.chat import get_chat_response
from app.utils import _get_message_history
from app.models.requests import ChatRequest
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
    Chat endpoint that returns direct responses.
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
    
    response = await get_chat_response(
        query=request.query,
        session_id=session_id,
        source_lang=request.source_lang,
        target_lang=request.target_lang,
        user_id=request.user_id,
        history=history,
        background_tasks=background_tasks
    )
    
    return {"response": response, "session_id": session_id} 