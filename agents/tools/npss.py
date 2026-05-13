"""
NPSS (National Pest Surveillance System) image analysis tool.

Integrates with NPSS API to analyze crop images for pest/disease identification.
The agent ONLY sees an image URL — never raw bytes. The tool downloads the image
from the provided URL before forwarding to NPSS.
"""
import os
import io
import re
from typing import Optional
import httpx
from pydantic_ai.tools import RunContext
from pydantic_ai import ModelRetry
from langfuse import observe
from helpers.utils import get_logger
from agents.deps import FarmerContext
from app.core.npss_geocodes import get_npss_location_ids
from app.core.cache import cache
from app.core.image_storage import mark_processed, cleanup_image

logger = get_logger(__name__)


def _format_npss_value(value, indent: int = 0) -> list[str]:
    prefix = "  " * indent

    if isinstance(value, dict):
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                lines.append(f"{prefix}- **{key}:**")
                lines.extend(_format_npss_value(nested, indent + 1))
            elif nested in (None, ""):
                lines.append(f"{prefix}- **{key}:**")
            else:
                lines.append(f"{prefix}- **{key}:** {nested}")
        return lines or [f"{prefix}-"]

    if isinstance(value, list):
        lines: list[str] = []
        if not value:
            return [f"{prefix}- None"]
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_format_npss_value(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
        return lines

    if value in (None, ""):
        return [f"{prefix}-"]

    return [f"{prefix}{value}"]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NPSS_BASE_URL = os.getenv("NPSS_BASE_URL", "https://npss.dac.gov.in/api3.0")
NPSS_USERNAME = os.getenv("NPSS_USERNAME", "")
NPSS_PASSWORD = os.getenv("NPSS_PASSWORD", "")
NPSS_TOKEN_CACHE_KEY = "npss:bearer_token"
NPSS_TOKEN_TTL_SECONDS = 25 * 60  # 25 minutes

GCS_MOVE_AFTER_PROCESS = os.getenv("GCS_MOVE_AFTER_PROCESS", "").strip().lower() in ("1", "true", "yes", "on")

# Default coordinates when user location is unavailable (geographic centre of India)
DEFAULT_LATITUDE = 20.5937
DEFAULT_LONGITUDE = 78.9629

# ---------------------------------------------------------------------------
# Image validation helpers
# ---------------------------------------------------------------------------

MAX_IMAGE_SIZE_MB = 10
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024


def _detect_mimetype(data: bytes) -> Optional[str]:
    """Detect image mimetype from magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

async def _get_npss_token() -> str:
    """Fetch a fresh NPSS bearer token."""
    if not NPSS_USERNAME or not NPSS_PASSWORD:
        raise ValueError("NPSS_USERNAME and NPSS_PASSWORD environment variables are required")

    token_url = f"{NPSS_BASE_URL}/api/Vistaar/token"
    logger.info(f"Fetching NPSS token from {token_url}")

    # Try multiple auth methods — NPSS API may expect different Content-Type
    auth_methods = [
        # Method 1: JSON body with credentials (ASP.NET Core APIs often expect this)
        {
            "json": {"username": NPSS_USERNAME, "password": NPSS_PASSWORD},
            "headers": {"Content-Type": "application/json"},
        },
        # Method 2: Form data with credentials
        {
            "data": {"username": NPSS_USERNAME, "password": NPSS_PASSWORD},
        },
        # Method 3: Basic auth in headers
        {
            "auth": (NPSS_USERNAME, NPSS_PASSWORD),
        },
    ]

    for attempt, kwargs in enumerate(auth_methods, 1):
        try:
            logger.info(f"Trying NPSS auth method {attempt}...")
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, timeout=30.0, **kwargs)
            response.raise_for_status()
            data = response.json()
            token = data.get("token") or data.get("access_token") or data.get("bearer_token")
            if not token:
                raise ValueError(f"Token response missing token field. Response: {data}")
            logger.info(f"NPSS token fetched successfully (method {attempt})")
            return token
        except httpx.HTTPStatusError as e:
            logger.warning(f"NPSS auth method {attempt} failed with status {e.response.status_code}")
            if attempt == len(auth_methods):
                logger.error(f"All NPSS auth methods failed. Last error: {e.response.status_code}: {e.response.text}")
                raise ModelRetry(f"NPSS token request failed with status {e.response.status_code}")
        except Exception as e:
            logger.warning(f"NPSS auth method {attempt} failed: {e}")
            if attempt == len(auth_methods):
                logger.error(f"Failed to fetch NPSS token: {e}")
                raise ModelRetry(f"NPSS token request failed: {e}")


async def _get_cached_npss_token() -> str:
    """Get NPSS token from cache or fetch a new one."""
    cached = await cache.get(NPSS_TOKEN_CACHE_KEY)
    if cached:
        logger.debug("Using cached NPSS token")
        return cached

    token = await _get_npss_token()
    await cache.set(NPSS_TOKEN_CACHE_KEY, token, ttl=NPSS_TOKEN_TTL_SECONDS)
    return token


# ---------------------------------------------------------------------------
# Image download from URL
# ---------------------------------------------------------------------------

async def _download_image(image_url: str) -> tuple[bytes, str]:
    """
    Download image bytes from a URL.
    Supports both absolute URLs and relative /api/image/{id} paths.
    """
    # Resolve relative URLs
    if image_url.startswith("/"):
        base_url = os.getenv("BASE_URL", "")
        if base_url:
            image_url = f"{base_url.rstrip('/')}{image_url}"
        else:
            raise ValueError(
                "Relative image URL provided but BASE_URL env var is not set. "
                "Cannot resolve local image path."
            )

    logger.info(f"Downloading image from {image_url}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        data = response.content
        mimetype = response.headers.get("content-type", "application/octet-stream")
        logger.info(f"Downloaded {len(data)} bytes, content-type: {mimetype}")
        return data, mimetype
    except httpx.HTTPStatusError as e:
        logger.error(f"Image download failed with status {e.response.status_code}: {e.response.text}")
        raise ModelRetry(f"Could not download image (status {e.response.status_code}). Please try again.")
    except Exception as e:
        logger.error(f"Image download failed: {e}")
        raise ModelRetry(f"Could not download image: {e}")


# ---------------------------------------------------------------------------
# NPSS analyze-image API
# ---------------------------------------------------------------------------

async def _call_npss_analyze(
    image_bytes: bytes,
    mimetype: str,
    state_id: str,
    district_id: str,
    sub_district_id: str,
    village_id: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> dict:
    """Call the NPSS analyze-image endpoint."""
    token = await _get_cached_npss_token()
    analyze_url = f"{NPSS_BASE_URL}/api/Vistaar/analyze-image"

    # Ensure filename has correct extension for NPSS
    ext = "jpg" if mimetype == "image/jpeg" else "png"
    filename = f"crop_image.{ext}"

    files = {
        "file": (filename, io.BytesIO(image_bytes), mimetype),
    }
    data = {
        "stateId": state_id,
        "districtId": district_id,
        "subDistrictId": sub_district_id,
        "villageId": village_id,
    }

    # NPSS API requires Latitude and Longitude — fall back to centre of India if unavailable
    lat = latitude if latitude is not None else DEFAULT_LATITUDE
    lng = longitude if longitude is not None else DEFAULT_LONGITUDE
    data["Latitude"] = str(lat)
    data["Longitude"] = str(lng)

    logger.info(
        f"Calling NPSS analyze-image for state={state_id}, district={district_id}, "
        f"subDistrict={sub_district_id}, village={village_id}, lat={latitude}, lng={longitude}"
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                analyze_url,
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                data=data,
                timeout=60.0,
            )
        response.raise_for_status()
        result = response.json()
        logger.info(f"NPSS analyze-image response: {result}")
        return result
    except httpx.HTTPStatusError as e:
        logger.error(f"NPSS analyze-image returned {e.response.status_code}: {e.response.text}")
        # If 401, invalidate token and retry once
        if e.response.status_code == 401:
            await cache.delete(NPSS_TOKEN_CACHE_KEY)
            raise ModelRetry("NPSS token expired. Retrying with fresh token.")
        raise ModelRetry(f"NPSS analyze failed with status {e.response.status_code}")
    except Exception as e:
        logger.error(f"NPSS analyze-image request failed: {e}")
        raise ModelRetry(f"NPSS analyze request failed: {e}")


# ---------------------------------------------------------------------------
# Image ID extraction from URL for cleanup
# ---------------------------------------------------------------------------

def _extract_image_id_from_url(image_url: str) -> Optional[str]:
    """Extract image ID from a local /api/image/{id} URL."""
    match = re.search(r"/api/image/([a-f0-9\-]+)", image_url)
    if match:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

@observe(name="tool:analyze_crop_image", as_type="tool")
async def analyze_crop_image(
    ctx: RunContext[FarmerContext],
    image_url: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    state_id: Optional[str] = None,
    district_id: Optional[str] = None,
    sub_district_id: Optional[str] = None,
    village_id: Optional[str] = None,
) -> str:
    """
    Analyze a crop image for pests or diseases using the National Pest Surveillance System (NPSS).

    Use this tool ONLY when the user has provided an image URL (e.g. from an uploaded crop photo)
    and wants to identify the pest or disease affecting it.

    Args:
        image_url: The URL of the uploaded crop image. This is REQUIRED.
        latitude: Optional user latitude for geocode lookup
        longitude: Optional user longitude for geocode lookup
        state_id: Optional NPSS State ID (overrides geocode lookup)
        district_id: Optional NPSS District ID
        sub_district_id: Optional NPSS SubDistrict ID
        village_id: Optional NPSS Village ID

    Returns:
        str: Diagnosis result from NPSS including pest name, crop, pathogen class, and description.
        Do NOT call `search_pests_diseases` automatically after this tool.
    """
    if not image_url or not image_url.strip():
        return (
            "No image URL was provided. "
            "Please upload a clear photo of the affected crop or plant part first."
        )

    image_url = image_url.strip()

    # Use lat/lng from context if not provided as parameters
    if latitude is None and ctx.deps.latitude is not None:
        latitude = ctx.deps.latitude
    if longitude is None and ctx.deps.longitude is not None:
        longitude = ctx.deps.longitude

    # Resolve location IDs if not explicitly provided
    if not all([state_id, district_id, sub_district_id, village_id]):
        geo = get_npss_location_ids(latitude, longitude)
        state_id = state_id or geo["state_id"]
        district_id = district_id or geo["district_id"]
        sub_district_id = sub_district_id or geo["sub_district_id"]
        village_id = village_id or geo["village_id"]

    # Download image from URL
    try:
        image_bytes, mimetype = await _download_image(image_url)
    except ModelRetry:
        raise
    except Exception as e:
        logger.error(f"Unexpected error downloading image: {e}")
        return (
            "The image could not be downloaded for analysis. "
            "Please try uploading it again."
        )

    # Validate size
    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        return (
            f"The uploaded image is too large ({len(image_bytes) // (1024 * 1024)} MB). "
            f"Please upload an image smaller than {MAX_IMAGE_SIZE_MB} MB."
        )

    # Validate / detect mimetype
    detected = _detect_mimetype(image_bytes)
    if detected:
        mimetype = detected
    elif not mimetype or mimetype == "application/octet-stream":
        return (
            "The uploaded file format could not be recognized. "
            "Please upload a JPEG or PNG image."
        )

    if mimetype == "image/webp":
        return (
            "WebP images are not supported by the pest analysis service. "
            "Please upload a JPEG or PNG image."
        )

    if mimetype not in ("image/jpeg", "image/png"):
        return (
            f"Unsupported image format: {mimetype}. "
            "Please upload a JPEG or PNG image."
        )

    # Extract image ID for post-processing cleanup
    image_id = _extract_image_id_from_url(image_url)
    if image_id:
        mark_processed(image_id)

    try:
        result = await _call_npss_analyze(
            image_bytes=image_bytes,
            mimetype=mimetype,
            state_id=state_id,
            district_id=district_id,
            sub_district_id=sub_district_id,
            village_id=village_id,
            latitude=latitude,
            longitude=longitude,
        )
    except ModelRetry:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in analyze_crop_image: {e}")
        return (
            "The pest analysis service encountered an unexpected error. "
            "Please try again with a clearer image."
        )
    finally:
        # Cleanup: delete temp file or move to GCS
        if image_id:
            cleanup_image(image_id, move_to_gcs=GCS_MOVE_AFTER_PROCESS)

    # Preserve the full NPSS payload in a readable structure instead of summarizing it.
    preferred_order = ["errors", "pest", "crop", "pathogenClass", "description"]
    ordered_keys = [key for key in preferred_order if key in result]
    ordered_keys.extend(key for key in result if key not in ordered_keys)

    lines = ["**NPSS Analysis Result**", ""]

    for key in ordered_keys:
        value = result.get(key)

        if isinstance(value, str):
            if value:
                if len(value) > 120 or "\n" in value:
                    lines.extend([f"**{key}:**", value, ""])
                else:
                    lines.append(f"**{key}:** {value}")
            else:
                lines.append(f"**{key}:**")
            continue

        lines.append(f"**{key}:**")
        lines.extend(_format_npss_value(value, indent=1))
        lines.append("")

    return "\n".join(lines).rstrip()
