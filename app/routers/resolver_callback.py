"""
Location Resolver callback: stores the keys in Redis for the request waiting on them.
"""
import json

from fastapi import APIRouter, HTTPException, Request

from app.core.cache import cache
from app.core.resolver_keys import (RESOLVER_KEYS_NAMESPACE, RESOLVER_KEYS_TTL_SECONDS, RESOLVER_PENDING_NAMESPACE,
                                    valid_signature)
from helpers.utils import get_logger

router = APIRouter(tags=["resolver-callback"])
logger = get_logger(__name__)


@router.post("/resolver/on_resolve")
async def resolver_on_resolve(request: Request):
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("x-resolver-timestamp"), request.headers.get("x-resolver-signature")):
        logger.warning("Resolver callback rejected: bad or missing signature")
        raise HTTPException(status_code=401, detail="Invalid resolver signature.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Body must be JSON.")
    ref = body.get("ref") if isinstance(body, dict) else None
    if not isinstance(ref, str) or not ref:
        raise HTTPException(status_code=400, detail="Missing ref.")
    if not await cache.get(ref, namespace=RESOLVER_PENDING_NAMESPACE):
        raise HTTPException(status_code=404, detail="Unknown ref.")
    answer = {
        "status": body.get("status"),
        "error": body.get("error"),
        "keys": [c["key"] for c in body.get("cells", []) if isinstance(c, dict) and c.get("key")],
    }
    await cache.set(ref, answer, ttl=RESOLVER_KEYS_TTL_SECONDS, namespace=RESOLVER_KEYS_NAMESPACE)
    logger.info(f"Resolver keys received for {ref}: {len(answer['keys'])} keys, status={answer['status']}")
    return {"ok": True}
