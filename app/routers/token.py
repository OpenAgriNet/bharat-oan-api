import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Header, HTTPException, status
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from pydantic import BaseModel, Field

from app.config import get_default_httpx_timeout, settings
from app.core.cache import cache
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/token", tags=["token"])
PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
PLAY_INTEGRITY_DECODE_URL_TEMPLATE = "https://playintegrity.googleapis.com/v1/{package_name}:decodeIntegrityToken"
_MAX_FUTURE_SKEW_MS = 10_000
_play_integrity_credentials = None
_play_integrity_credentials_lock = asyncio.Lock()


def _resolve_private_key_path() -> Path:
    if settings.jwt_private_key_path:
        configured_path = Path(settings.jwt_private_key_path)
        return configured_path if configured_path.is_absolute() else settings.base_dir / configured_path
    return settings.base_dir / "private_key.pem"


private_key = None
private_key_path = _resolve_private_key_path()
if private_key_path.exists():
    try:
        with open(private_key_path, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None
            )
        logger.info(f"Successfully loaded JWT Private Key from: {private_key_path}")
    except Exception as e:
        logger.error(f"Failed to load JWT Private Key: {str(e)}")
        private_key = None
else:
    logger.warning(f"JWT Private Key file not found at: {private_key_path}")


class AuthRequest(BaseModel):
    mobile: Optional[str] = Field(None, description="Mobile number")
    name: Optional[str] = Field(None, description="User name")
    role: Optional[str] = Field(None, description="User role")
    metadata: Optional[str] = Field(None, description="Additional metadata as string")


class AuthResponse(BaseModel):
    token: str = Field(..., description="JWT token")
    expires_in: int = Field(..., description="Token expiration time in seconds")


class StaticAuthResponse(BaseModel):
    token: str = Field(..., description="JWT token")
    expires_in: Optional[int] = Field(None, description="Token expiration time in seconds")


class PlayIntegrityAuthRequest(BaseModel):
    integrity_token: str = Field(..., description="Play Integrity token generated on device")
    nonce: str = Field(..., description="Nonce used when requesting Play Integrity token")
    mobile: Optional[str] = Field(None, description="Mobile number")


def _issue_jwt_token(
    mobile: Optional[str] = None,
    name: Optional[str] = None,
    role: Optional[str] = None,
    metadata: Optional[str] = None,
    expires_minutes: Optional[int] = None,
    include_issued_at: bool = True,
) -> tuple[str, Optional[int]]:
    if private_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
        )

    try:
        now = datetime.utcnow()
        payload = {}
        if mobile is not None:
            payload["mobile"] = mobile
        if name is not None:
            payload["name"] = name
        if role is not None:
            payload["role"] = role
        if metadata is not None:
            payload["metadata"] = metadata

        if include_issued_at:
            payload["iat"] = int(now.timestamp())

        expires_in = None
        if expires_minutes is not None:
            exp = now + timedelta(minutes=expires_minutes)
            payload["exp"] = int(exp.timestamp())
            expires_in = int(exp.timestamp() - now.timestamp())

        token = jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)
        return token, expires_in
    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create JWT token: {str(e)}"
        )


def _resolve_service_account_path() -> Path:
    if not settings.play_integrity_service_account_file:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Play Integrity service account file is not configured."
        )
    configured_path = Path(settings.play_integrity_service_account_file)
    return configured_path if configured_path.is_absolute() else settings.base_dir / configured_path


async def _get_play_integrity_access_token() -> str:
    global _play_integrity_credentials

    async with _play_integrity_credentials_lock:
        if _play_integrity_credentials is None:
            service_account_path = _resolve_service_account_path()
            if not service_account_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Play Integrity service account file not found: {service_account_path}"
                )
            _play_integrity_credentials = service_account.Credentials.from_service_account_file(
                str(service_account_path),
                scopes=[PLAY_INTEGRITY_SCOPE],
            )

        if not _play_integrity_credentials.valid or not _play_integrity_credentials.token:
            await asyncio.to_thread(_play_integrity_credentials.refresh, GoogleAuthRequest())

        if not _play_integrity_credentials.token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to fetch access token for Play Integrity API."
            )

        return _play_integrity_credentials.token


async def _decode_play_integrity_token(integrity_token: str) -> Dict[str, Any]:
    if not settings.play_integrity_package_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PLAY_INTEGRITY_PACKAGE_NAME is not configured."
        )

    access_token = await _get_play_integrity_access_token()
    url = PLAY_INTEGRITY_DECODE_URL_TEMPLATE.format(package_name=settings.play_integrity_package_name)
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {"integrity_token": integrity_token}

    try:
        async with httpx.AsyncClient(timeout=get_default_httpx_timeout()) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Play Integrity decode failed | status_code=%s body=%s",
            e.response.status_code if e.response else "unknown",
            e.response.text[:500] if e.response else "no response",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Play Integrity verification failed."
        )
    except httpx.HTTPError as e:
        logger.error(f"Play Integrity API call failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Play Integrity API unavailable."
        )

    decoded = response.json()
    token_payload = decoded.get("tokenPayloadExternal")
    if not token_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Play Integrity token payload."
        )
    return token_payload


def _validate_play_integrity_payload(token_payload: Dict[str, Any], expected_nonce: str) -> None:
    request_details = token_payload.get("requestDetails") or {}
    package_name = request_details.get("requestPackageName")
    if package_name != settings.play_integrity_package_name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid package name in Play Integrity verdict."
        )

    actual_nonce = request_details.get("nonce")
    if not actual_nonce or not hmac.compare_digest(actual_nonce, expected_nonce):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid nonce in Play Integrity verdict."
        )

    timestamp_ms = request_details.get("timestampMillis")
    try:
        verdict_timestamp_ms = int(timestamp_ms)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Play Integrity timestamp."
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    max_age_ms = settings.play_integrity_freshness_seconds * 1000
    if now_ms - verdict_timestamp_ms > max_age_ms:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Play Integrity token is too old."
        )
    if verdict_timestamp_ms - now_ms > _MAX_FUTURE_SKEW_MS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Play Integrity token timestamp is invalid."
        )

    app_integrity = token_payload.get("appIntegrity") or {}
    if app_integrity.get("appRecognitionVerdict") != "PLAY_RECOGNIZED":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App recognition failed Play Integrity validation."
        )

    device_integrity = token_payload.get("deviceIntegrity") or {}
    device_verdicts = device_integrity.get("deviceRecognitionVerdict") or []
    if "MEETS_DEVICE_INTEGRITY" not in device_verdicts:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device integrity check failed."
        )

    account_details = token_payload.get("accountDetails") or {}
    licensing_verdict = account_details.get("appLicensingVerdict")
    if licensing_verdict and licensing_verdict != "LICENSED":
        logger.warning(
            "Play Integrity licensing verdict not enforced | verdict=%s",
            licensing_verdict
        )


async def _ensure_nonce_not_reused(nonce: str) -> None:
    cache_key = f"integrity_nonce:{nonce}"
    try:
        existing = await cache.get(cache_key)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Nonce already used."
            )
        await cache.set(cache_key, True, ttl=settings.play_integrity_freshness_seconds)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Nonce replay check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Replay protection is unavailable."
        )


@router.post("", status_code=status.HTTP_200_OK, response_model=AuthResponse)
async def create_auth_token(request: Optional[AuthRequest] = None):
    """
    Create and return an encrypted JWT token.
    The token contains mobile, name, role, and metadata.
    Uses private key .pem file for signing the JWT.
    Request body is optional - if not provided, uses default values.
    """
    if private_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
        )

    try:
        # Use request data if provided, otherwise use defaults
        mobile = request.mobile if request and request.mobile else "1111111111"
        name = request.name if request and request.name else "guest"
        role = request.role if request and request.role else "public"
        metadata = request.metadata if request and request.metadata else ""

        # Create JWT payload
        now = datetime.utcnow()
        exp = now + timedelta(minutes=settings.jwt_expiry_minutes)

        payload = {
            "mobile": mobile,
            "name": name,
            "role": role,
            "metadata": metadata,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp())
        }

        # Encode JWT token using private key
        token = jwt.encode(
            payload,
            private_key,
            algorithm=settings.jwt_algorithm
        )

        logger.debug("JWT token created successfully")

        # Calculate expiration time in seconds
        expires_in = int(exp.timestamp() - now.timestamp())

        return AuthResponse(
            token=token,
            expires_in=expires_in
        )

    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create JWT token: {str(e)}"
        )


@router.post("/api-key", status_code=status.HTTP_200_OK, response_model=StaticAuthResponse)
async def create_auth_token_with_api_key(
    api_key: str = Header(..., alias="X-API-Key", description="Client API key"),
):
    """
    Validate static API key from env and issue expiring JWT.
    """
    configured_api_key = settings.api_key_auth_token
    if not configured_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY_AUTH_TOKEN is not configured."
        )

    if not hmac.compare_digest(api_key, configured_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key."
        )

    token, expires_in = _issue_jwt_token(
        expires_minutes=settings.jwt_expiry_minutes,
        include_issued_at=True,
    )
    return StaticAuthResponse(token=token, expires_in=expires_in)


@router.post("/play-integrity", status_code=status.HTTP_200_OK, response_model=AuthResponse)
async def create_auth_token_with_play_integrity(request: PlayIntegrityAuthRequest):
    """
    Validate Google Play Integrity verdict and issue JWT.
    """
    token_payload = await _decode_play_integrity_token(request.integrity_token)
    _validate_play_integrity_payload(token_payload, request.nonce)
    await _ensure_nonce_not_reused(request.nonce)

    mobile = request.mobile if request.mobile else "verified_device"
    token, expires_in = _issue_jwt_token(
        mobile=mobile,
        name=settings.play_integrity_default_name,
        role=settings.play_integrity_default_role,
        metadata=settings.play_integrity_default_metadata,
        expires_minutes=settings.jwt_expiry_minutes,
    )
    if expires_in is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute token expiry."
        )
    return AuthResponse(token=token, expires_in=expires_in)
