"""
Location Resolver settings and callback signature check.
"""
import hashlib
import hmac
import os
import time
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

RESOLVER_KEYS_NAMESPACE = "resolver-keys"        # answers from the resolver, by ref
RESOLVER_PENDING_NAMESPACE = "resolver-pending"  # refs this service is waiting for
RESOLVER_KEYS_TTL_SECONDS = 600

RESOLVER_URL = (os.getenv("RESOLVER_URL") or "").rstrip("/")
RESOLVER_CALLBACK_URL = os.getenv("RESOLVER_CALLBACK_URL") or ""
RESOLVER_API_KEY = os.getenv("RESOLVER_API_KEY") or ""
RESOLVER_SUBSCRIBER_ID = os.getenv("RESOLVER_SUBSCRIBER_ID") or None
RESOLVER_WAIT_SECONDS = float(os.getenv("RESOLVER_WAIT_SECONDS", "8"))
SIGNATURE_MAX_AGE_SECONDS = 300


def resolver_configured() -> bool:
    return bool(RESOLVER_URL and RESOLVER_CALLBACK_URL)


def request_headers() -> dict:
    return {"X-API-Key": RESOLVER_API_KEY} if RESOLVER_API_KEY else {}


def valid_signature(raw: bytes, timestamp: Optional[str], signature: Optional[str]) -> bool:
    """HMAC-SHA256(RESOLVER_API_KEY, "<timestamp>." + raw body), sent by the resolver as "sha256=<hex>"."""
    if not RESOLVER_API_KEY:
        return True  # resolver running without keys (local development only)
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(time.time() - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS:
        return False
    good = "sha256=" + hmac.new(RESOLVER_API_KEY.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, signature)
