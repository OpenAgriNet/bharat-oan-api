from fastapi import APIRouter, HTTPException, status
from app.utils import cache
from app.config import settings
import time
from typing import Dict, Any

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/master-catalog", status_code=status.HTTP_200_OK)
async def master_catalog_status():
    """
    Debug/inspection: what search_schemes routing actually sees right now —
    the merged static+live scheme list, plus the raw Redis snapshot metadata
    docs-pipeline last pushed (version/updated_at/tier). Answers "what schemes
    are synced" without needing redis-cli or a Redis GUI.
    """
    from helpers.master_catalog import get_master_catalog_snapshot, _tier_for_environment
    from helpers.scheme_qdrant_search import get_builtin_scheme_list

    tier = _tier_for_environment()
    snapshot = get_master_catalog_snapshot(tier)
    scheme_list = sorted(get_builtin_scheme_list(), key=lambda item: item["scheme_code"])

    return {
        "tier": tier,
        "redis_snapshot_found": snapshot is not None,
        "snapshot_version": (snapshot or {}).get("version"),
        "snapshot_updated_at": (snapshot or {}).get("updated_at"),
        "live_synced_entry_count": len((snapshot or {}).get("entries", [])),
        "resolved_scheme_count": len(scheme_list),
        "resolved_schemes": scheme_list,
        "note": (
            "resolved_schemes is what search_schemes routing and the system prompt "
            "actually use this turn — static seed merged with live Redis data. "
            "If redis_snapshot_found is false, this is the static-only fallback."
        ),
    }

# Track when the application started
START_TIME = time.time()

async def check_cache_connection() -> Dict[str, Any]:
    """Check Redis cache connection"""
    try:
        test_key = "health_check_test"
        test_value = "test"
        await cache.set(test_key, test_value, ttl=5)
        cached_value = await cache.get(test_key)
        return {
            "status": "healthy" if cached_value == test_value else "unhealthy",
            "latency_ms": 0  # TODO: Add actual latency measurement
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness():
    """
    Liveness probe - simple check to see if the application is running
    Used by Kubernetes to know when to restart the pod
    """
    return {"status": "alive"}

@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness():
    """
    Readiness probe - checks if the application is ready to handle traffic
    Used by Kubernetes to know when to send traffic to the pod
    """
    cache_health = await check_cache_connection()
    
    if cache_health["status"] != "healthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not ready", "cache": cache_health}
        )
    
    return {"status": "ready", "cache": cache_health}

@router.get("/", status_code=status.HTTP_200_OK)
async def health_check():
    """
    Health check that includes:
    - Application metadata (version, uptime)
    - Service dependencies (Redis cache)
    """
    cache_health = await check_cache_connection()
    uptime_seconds = int(time.time() - START_TIME)
    
    health_status = {
        "app": {
            "name": settings.app_name,
            "environment": settings.environment,
            "uptime_seconds": uptime_seconds
        },
        "dependencies": {
            "cache": cache_health
        }
    }
    
    # If any critical dependency is unhealthy, return 503
    if cache_health["status"] != "healthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=health_status
        )
    
    return health_status 