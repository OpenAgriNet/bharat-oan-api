import jwt
from typing import Any, Optional

from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param
from helpers.utils import get_logger
from app.config import settings  # Import the application settings

load_dotenv()

logger = get_logger(__name__)


class OptionalOAuth2PasswordBearer(OAuth2PasswordBearer):
    """OAuth2 scheme that allows missing Bearer tokens when auth is disabled."""

    async def __call__(self, request: Request) -> Optional[str]:
        if settings.disable_auth:
            authorization = request.headers.get("Authorization")
            if not authorization:
                return None
            scheme, param = get_authorization_scheme_param(authorization)
            if scheme.lower() != "bearer":
                return None
            return param
        return await super().__call__(request)


# OAuth2 scheme for FastAPI - optional when DISABLE_AUTH / local env is set
oauth2_scheme = OptionalOAuth2PasswordBearer(tokenUrl="token")

# Construct the absolute path to the public key using settings
public_key_path = settings.base_dir / settings.jwt_public_key_path

with open(public_key_path, "rb") as key_file:
    public_key = serialization.load_pem_public_key(key_file.read())
logger.info(f"Successfully loaded JWT Public Key from: {public_key_path}")

DEFAULT_AUTH_CHANNEL = "BharatVistaar"


def _disabled_auth_user() -> dict[str, Any]:
    return {
        "sub": settings.disable_auth_mobile,
        "mobile": settings.disable_auth_mobile,
        "name": "disabled_auth_user",
        "email": None,
        "role": None,
        "farmer_id": None,
        "unique_id": None,
        "locations": [],
        "channel": DEFAULT_AUTH_CHANNEL,
        "client_code": None,
        "auth_source": "disable_auth",
        "is_guest_user": False,
        "telemetry_context": {},
        "metadata": {},
    }


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> dict[str, Any]:
    """
    FastAPI dependency to get current authenticated user from JWT token.
    When settings.disable_auth is True (DISABLE_AUTH=true or local/dev env),
    returns a stub user without requiring a Bearer token.
    """
    if settings.disable_auth:
        logger.info("Auth disabled - bypassing JWT validation")
        return _disabled_auth_user()

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if public_key is None:
        logger.error("JWT Public Key is not loaded, cannot verify tokens.")
        raise credentials_exception

    try:
        decoded_token = jwt.decode(
            token,
            public_key,
            algorithms=[settings.jwt_algorithm],
            options={
                "verify_signature": True,
                "verify_aud": False,
                "verify_iss": False,
            },
        )

        logger.info("Successfully decoded token")
        mobile = decoded_token.get("mobile")
        if mobile is None and decoded_token.get("is_guest_user") is not True:
            logger.warning("No mobile number found in token")
        #            raise credentials_exception

        channel = decoded_token.get("channel") or DEFAULT_AUTH_CHANNEL
        telemetry_context = decoded_token.get("telemetry_context") or {}
        metadata = decoded_token.get("metadata") or {}

        return {
            "sub": decoded_token.get("sub"),
            "mobile": mobile,
            "name": decoded_token.get("name"),
            "email": decoded_token.get("email"),
            "role": decoded_token.get("role"),
            "farmer_id": decoded_token.get("farmer_id"),
            "unique_id": decoded_token.get("unique_id"),
            "locations": decoded_token.get("locations") or [],
            "channel": channel,
            "client_code": decoded_token.get("client_code"),
            "auth_source": decoded_token.get("auth_source"),
            "is_guest_user": decoded_token.get("is_guest_user") is True,
            "telemetry_context": telemetry_context if isinstance(telemetry_context, dict) else {},
            "metadata": metadata if isinstance(metadata, dict) else {},
        }

    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except Exception as e:
        logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
