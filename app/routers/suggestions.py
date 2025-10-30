from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from app.models.requests import SuggestionsRequest
from app.utils import get_cache
from app.auth.jwt_auth import get_current_user

router = APIRouter(prefix="/suggest", tags=["suggest"])

@router.get("/")
async def suggest(
    request: SuggestionsRequest = Depends(),
    current_user: str = Depends(get_current_user)
):
    """
    Get suggestions for a conversation session.
    Suggestions are created asynchronously in the chat service.
    """
    cache_key = f"suggestions_{request.session_id}_{request.target_lang}"
    suggestions = await get_cache(cache_key) or []
    return JSONResponse(suggestions)