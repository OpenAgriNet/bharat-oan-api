"""Auth for the voice endpoint.

Same JWT verification as chat (app.auth.jwt_auth). VOICE_AUTH_ENABLED=false keeps the
old voice behaviour of accepting requests without a bearer token; a token that is sent
is still verified. Chat endpoints are never affected by this setting.
"""
from typing import Any, Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param

from app.auth import jwt_auth
from app.config import settings


class _VoiceBearer(OAuth2PasswordBearer):
    async def __call__(self, request: Request) -> Optional[str]:
        if settings.voice_auth_enabled:
            return await super().__call__(request)
        scheme, token = get_authorization_scheme_param(request.headers.get("Authorization"))
        return token if scheme.lower() == "bearer" and token else None


voice_bearer = _VoiceBearer(tokenUrl="token")


async def get_voice_user(token: Optional[str] = Depends(voice_bearer)) -> Optional[dict[str, Any]]:
    if token is None:
        return None  # Only reachable with VOICE_AUTH_ENABLED=false.
    return await jwt_auth.get_current_user(token)
