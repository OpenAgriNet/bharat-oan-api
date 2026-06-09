import asyncio
import hmac
import json
import os
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
_play_integrity_credentials: Dict[str, Any] = {}
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
    fingerprint_id: Optional[str] = Field(None, description="Client fingerprint/device identifier")
    metadata: Optional[Any] = Field(None, description="Additional metadata")


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
    client_code: str = Field(..., description="Client code for selecting per-client config")


class ApiKeyAuthRequest(BaseModel):
    client_code: str = Field(..., description="Client code for selecting per-client API key")


def _normalize_client_code(client_code: str) -> str:
    return client_code.strip().upper().replace("-", "_").replace(" ", "_")


def _require_client_code(client_code: Optional[str], context: str) -> str:
    if client_code is None or not client_code.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"client_code is required for {context}."
        )
    return client_code.strip()


def _issue_jwt_token(
    mobile: Optional[str] = None,
    name: Optional[str] = None,
    role: Optional[str] = None,
    metadata: Optional[Any] = None,
    channel: Optional[str] = None,
    expires_minutes: Optional[int] = None,
    include_issued_at: bool = True,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> tuple[str, Optional[int]]:
    if private_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
        )

    try:
        now = datetime.now(timezone.utc)
        payload = {}
        if mobile is not None:
            payload["mobile"] = mobile
        if name is not None:
            payload["name"] = name
        if role is not None:
            payload["role"] = role
        if metadata is not None:
            payload["metadata"] = metadata
        if channel is not None:
            payload["channel"] = channel
        if extra_claims:
            payload.update(extra_claims)

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


def _resolve_service_account_path(client_code: Optional[str] = None) -> Path:
    # If client_code is provided, use client-specific file path
    if client_code:
        normalized = _normalize_client_code(client_code)

        service_account_base_path = getattr(settings, "play_integrity_service_account_base_path", None)
        if service_account_base_path:
            candidate = Path(f"{service_account_base_path}{normalized.lower()}.json")
            resolved = candidate if candidate.is_absolute() else settings.base_dir / candidate
            if resolved.exists():
                return resolved
            logger.warning(
                "Client-specific Play Integrity service account not found from base path: %s",
                str(resolved),
            )

        base_path = Path(settings.play_integrity_service_account_file).parent if settings.play_integrity_service_account_file else Path("secrets")
        candidate_names = [
            f"play-integrity-service-account-{client_code}.json",
            f"play-integrity-service-account-{normalized.lower()}.json",
            f"play-integrity-service-account-{normalized.lower().replace('_', '-')}.json",
        ]
        for candidate_name in candidate_names:
            candidate = base_path / candidate_name
            resolved = candidate if candidate.is_absolute() else settings.base_dir / candidate
            if resolved.exists():
                return resolved

        logger.warning(
            "Client-specific Play Integrity service account not found for client_code=%s, falling back to default",
            client_code,
        )

    # Fall back to default service account file
    if not settings.play_integrity_service_account_file:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Play Integrity service account file is not configured."
        )
    configured_path = Path(settings.play_integrity_service_account_file)
    return configured_path if configured_path.is_absolute() else settings.base_dir / configured_path


def _resolve_play_integrity_package_name(client_code: Optional[str] = None) -> str:
    if client_code:
        normalized = _normalize_client_code(client_code)
        env_key = f"{settings.play_integrity_package_name_prefix}{normalized}"
        package_name = os.getenv(env_key)
        if package_name and package_name.strip():
            return package_name.strip()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Missing Play Integrity package configuration for client_code '{client_code}' ({env_key})."
        )

    if not settings.play_integrity_package_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Play Integrity package name is not configured."
        )
    return settings.play_integrity_package_name


def _resolve_api_key(client_code: Optional[str] = None) -> str:
    if client_code:
        normalized = _normalize_client_code(client_code)
        env_key = f"{settings.api_key_auth_token_prefix}{normalized}"
        client_key = os.getenv(env_key)
        if client_key and client_key.strip():
            return client_key.strip()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Missing API key configuration for client_code '{client_code}' ({env_key})."
        )

    if not settings.api_key_auth_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY_AUTH_TOKEN is not configured."
        )
    return settings.api_key_auth_token


async def _get_play_integrity_access_token(client_code: Optional[str] = None) -> str:
    global _play_integrity_credentials

    client_key = _normalize_client_code(client_code) if client_code else "default"

    async with _play_integrity_credentials_lock:
        if client_key not in _play_integrity_credentials:
            service_account_path = _resolve_service_account_path(client_code)
            if not service_account_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Play Integrity service account file not found: {service_account_path}"
                )

            # Load JSON and replace private_key from env if client_code is provided
            with open(service_account_path, "r", encoding="utf-8") as f:
                service_account_info = json.load(f)

            # If client_code exists, try to load private key from environment variable
            if client_code:
                normalized = _normalize_client_code(client_code)
                env_key = f"{settings.play_integrity_private_key_prefix}{normalized}"
                private_key_from_env = os.getenv(env_key)

                if private_key_from_env:
                    # Replace the private_key in JSON with env value
                    service_account_info["private_key"] = private_key_from_env.replace("\\n", "\n")
                    logger.info(f"Loaded private key for {client_key} from env var {env_key}")

            _play_integrity_credentials[client_key] = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=[PLAY_INTEGRITY_SCOPE],
            )

        credentials = _play_integrity_credentials[client_key]
        if not credentials.valid or not credentials.token:
            await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())

        if not credentials.token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to fetch access token for Play Integrity API."
            )

        return credentials.token


async def _decode_play_integrity_token(
    integrity_token: str,
    package_name: str,
    client_code: Optional[str] = None,
) -> Dict[str, Any]:
    access_token = await _get_play_integrity_access_token(client_code)
    url = PLAY_INTEGRITY_DECODE_URL_TEMPLATE.format(package_name=package_name)
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {"integrityToken": integrity_token}

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


def _validate_play_integrity_payload(
    token_payload: Dict[str, Any],
    expected_nonce: str,
    expected_package_name: str,
) -> None:
    request_details = token_payload.get("requestDetails") or {}
    package_name = request_details.get("requestPackageName")
    if package_name != expected_package_name:
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


def _parse_metadata_field(raw_metadata: Any) -> Dict[str, Any]:
    if isinstance(raw_metadata, dict):
        return dict(raw_metadata)
    if isinstance(raw_metadata, str):
        try:
            parsed_metadata = json.loads(raw_metadata)
            if isinstance(parsed_metadata, dict):
                return dict(parsed_metadata)
            return {"raw": raw_metadata}
        except json.JSONDecodeError:
            return {"raw": raw_metadata}
    return {"raw": str(raw_metadata)}


def _finalize_auth_metadata(
    metadata: Dict[str, Any],
    fingerprint_id: Optional[str],
) -> Dict[str, Any]:
    if fingerprint_id:
        metadata["fingerprint_id"] = fingerprint_id
    metadata["surface"] = metadata.get("surface") or "public_chat"
    return metadata


def _parse_auth_request_metadata(request: Optional[AuthRequest]) -> tuple[Dict[str, Any], Optional[str]]:
    fingerprint_id = request.fingerprint_id if request and request.fingerprint_id else None
    if not request or not request.metadata:
        return _finalize_auth_metadata({}, fingerprint_id), fingerprint_id

    metadata = _parse_metadata_field(request.metadata)
    return _finalize_auth_metadata(metadata, fingerprint_id), fingerprint_id


def _build_guest_auth_payload(
    request: Optional[AuthRequest],
    metadata: Dict[str, Any],
    fingerprint_id: Optional[str],
) -> tuple[Dict[str, Any], int]:
    mobile = request.mobile if request and request.mobile else None
    name = request.name if request and request.name else "guest"
    role = request.role if request and request.role else "public"
    channel = "BharatVistaar"
    guest_sub = f"guest:{fingerprint_id}" if fingerprint_id else "guest:anon"

    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_expiry_minutes)
    payload = {
        "sub": guest_sub,
        "name": name,
        "role": role,
        "channel": channel,
        "client_code": channel,
        "auth_source": "guest_token",
        "is_guest_user": True,
        "telemetry_context": {
            "uid": "guest",
            "did": fingerprint_id,
            "channel": channel,
            "pdata_id": "BharatVistaar",
            "pdata_ver": "v0.1",
        },
        "metadata": metadata,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if mobile:
        payload["mobile"] = mobile

    expires_in = int(exp.timestamp() - now.timestamp())
    return payload, expires_in


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
        metadata, fingerprint_id = _parse_auth_request_metadata(request)
        payload, expires_in = _build_guest_auth_payload(request, metadata, fingerprint_id)
        token = jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)
        logger.debug("JWT token created successfully")
        return AuthResponse(token=token, expires_in=expires_in)

    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create JWT token: {str(e)}"
        )


@router.post("/api-key", status_code=status.HTTP_200_OK, response_model=StaticAuthResponse)
async def create_auth_token_with_api_key(
    request: ApiKeyAuthRequest,
    api_key: str = Header(..., alias="X-API-Key", description="Client API key"),
):
    """
    Validate static API key from env and issue expiring JWT.
    """
    client_code = _require_client_code(request.client_code, "API key authentication")
    resolved_api_key = _resolve_api_key(client_code)

    if not hmac.compare_digest(api_key, resolved_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key."
        )

    token, expires_in = _issue_jwt_token(
        channel=client_code,
        expires_minutes=settings.jwt_expiry_minutes,
        include_issued_at=True,
    )
    return StaticAuthResponse(token=token, expires_in=expires_in)


@router.post("/play-integrity", status_code=status.HTTP_200_OK, response_model=AuthResponse)
async def create_auth_token_with_play_integrity(request: PlayIntegrityAuthRequest):
    """
    Validate Google Play Integrity verdict and issue JWT.
    """
    client_code = _require_client_code(request.client_code, "Play Integrity authentication")
    package_name = _resolve_play_integrity_package_name(client_code)
    token_payload = await _decode_play_integrity_token(
        request.integrity_token,
        package_name=package_name,
        client_code=client_code,
    )
    _validate_play_integrity_payload(token_payload, request.nonce, expected_package_name=package_name)
    await _ensure_nonce_not_reused(request.nonce)

    mobile = request.mobile if request.mobile else "verified_device"
    token, expires_in = _issue_jwt_token(
        mobile=mobile,
        name=settings.play_integrity_default_name,
        role=settings.play_integrity_default_role,
        metadata=settings.play_integrity_default_metadata,
        channel=client_code,
        expires_minutes=settings.jwt_expiry_minutes,
    )
    if expires_in is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute token expiry."
        )
    return AuthResponse(token=token, expires_in=expires_in)


def validate_multi_provider_auth_config() -> None:
    api_key_prefix = settings.api_key_auth_token_prefix
    play_package_prefix = settings.play_integrity_package_name_prefix
    play_private_key_prefix = settings.play_integrity_private_key_prefix

    def _has_provider_suffix(env_key: str, prefix: str) -> bool:
        if not env_key.startswith(prefix):
            return False
        suffix = env_key[len(prefix):]
        return bool(suffix) and suffix != "PREFIX"

    provider_errors = []

    for env_key, env_value in os.environ.items():
        if _has_provider_suffix(env_key, api_key_prefix) and not (env_value or "").strip():
            provider_errors.append(f"{env_key} is empty")
        if _has_provider_suffix(env_key, play_package_prefix) and not (env_value or "").strip():
            provider_errors.append(f"{env_key} is empty")
        if _has_provider_suffix(env_key, play_private_key_prefix) and not (env_value or "").strip():
            provider_errors.append(f"{env_key} is empty")

    if provider_errors:
        raise RuntimeError(
            "Invalid multi-provider auth configuration: " + "; ".join(provider_errors)
        )
