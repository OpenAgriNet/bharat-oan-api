"""Request headers for the telemetry ingestion API.

Shared by the chat and voice senders; each passes its own credentials and origin,
so one channel's settings never leak into the other's requests.
"""
import base64
import hashlib
import hmac
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def build_telemetry_auth_token(
    token: Optional[str],
    key: Optional[str],
    secret: Optional[str],
) -> Optional[str]:
    """Return a static token, or an HS256 JWT signed from key/secret, or None."""
    if token:
        return token
    if not key or not secret:
        return None

    def encode(value: Dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    unsigned = f"{encode({'typ': 'JWT', 'alg': 'HS256'})}." + encode(
        {"iss": key, "iat": None, "exp": None, "aud": "", "sub": ""}
    )
    signature = hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest()
    return f"{unsigned}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def build_telemetry_headers(
    *,
    token: Optional[str] = None,
    key: Optional[str] = None,
    secret: Optional[str] = None,
    origin: Optional[str] = None,
    referer: Optional[str] = None,
) -> Dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "dataType": "json",
    }
    auth_token = build_telemetry_auth_token(token, key, secret)
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if origin:
        headers["Origin"] = origin
    if referer:
        headers["Referer"] = referer
    return headers
