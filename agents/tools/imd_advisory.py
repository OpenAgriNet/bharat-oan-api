"""
IMD district agromet advisory tool: farmer location -> resolver keys -> imd_agromet DB.
"""
import asyncio
import datetime
import os
import threading
import uuid
from typing import List, Optional

import httpx
import psycopg2
from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from app.config import DEFAULT_HTTP_TIMEOUT
from app.core.cache import cache
from app.core.resolver_keys import (RESOLVER_CALLBACK_URL, RESOLVER_KEYS_NAMESPACE, RESOLVER_KEYS_TTL_SECONDS,
                                    RESOLVER_PENDING_NAMESPACE, RESOLVER_SUBSCRIBER_ID, RESOLVER_URL,
                                    RESOLVER_WAIT_SECONDS, request_headers, resolver_configured)
from helpers.utils import get_logger

load_dotenv()
logger = get_logger(__name__)

IMD_DB_URL = os.getenv("IMD_DB_URL")

POINT_KEYS_NAMESPACE = "resolver-point-keys"
POINT_KEYS_TTL_SECONDS = 30 * 24 * 3600
POINT_RADIUS_KM = 0.1


async def _location_keys(latitude: float, longitude: float) -> Optional[List[str]]:
    """Resolver keys for a ~100 m area around the point (cached per point)."""
    point = f"{latitude:.3f},{longitude:.3f}"
    cached = await cache.get(point, namespace=POINT_KEYS_NAMESPACE)
    if cached:
        return cached

    ref = f"bv-{uuid.uuid4().hex}"
    body = {
        "ref": ref,
        "callback_url": RESOLVER_CALLBACK_URL,
        "area": {"method": "point_radius", "input": {"lat": latitude, "lon": longitude, "radius_km": POINT_RADIUS_KM}},
    }
    if RESOLVER_SUBSCRIBER_ID:
        body["subscriber_id"] = RESOLVER_SUBSCRIBER_ID
    # mark the ref as ours before asking, so the callback route accepts the answer
    await cache.set(ref, True, ttl=RESOLVER_KEYS_TTL_SECONDS, namespace=RESOLVER_PENDING_NAMESPACE)
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(f"{RESOLVER_URL}/resolver/v1/resolve", json=body, headers=request_headers())
        response.raise_for_status()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + RESOLVER_WAIT_SECONDS
    answer = None
    while loop.time() < deadline:
        answer = await cache.get(ref, namespace=RESOLVER_KEYS_NAMESPACE)
        if answer:
            break
        await asyncio.sleep(0.25)
    if not answer:
        logger.warning(f"Resolver did not call back within {RESOLVER_WAIT_SECONDS}s for {ref}")
        return None
    if answer.get("status") != "ok" or not answer.get("keys"):
        logger.warning(f"Resolver could not resolve {ref}: {answer.get('error')}")
        return None
    await cache.set(point, answer["keys"], ttl=POINT_KEYS_TTL_SECONDS, namespace=POINT_KEYS_NAMESPACE)
    return answer["keys"]


_pool: Optional[ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _lookup_sync(keys: List[str]) -> dict:
    """imd_lookup() on the imd_agromet DB: districts covering the keys + their current bulletin rows."""
    global _pool
    if not IMD_DB_URL:
        raise RuntimeError("IMD_DB_URL is not set")
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(1, 4, IMD_DB_URL, connect_timeout=5)
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT imd_lookup(%s)", (keys,))
            return cur.fetchone()[0]
    finally:
        conn.rollback()
        _pool.putconn(conn)


def _filter_by_crop(rows: List[dict], crop: Optional[str], districts: str) -> tuple:
    """Rows for the given crop; all rows with a note when the bulletin has none for it."""
    if not crop:
        return rows, ""
    matched = [r for r in rows if (r.get("crop") or "").lower() == crop.strip().lower()]
    if matched:
        return matched, ""
    logger.warning(f"IMD advisory: crop {crop!r} not matched in the bulletin for {districts}")
    return rows, f"(No advisory specifically for {crop} in this bulletin; showing the full district advisory.)"


def _display_date(value: str) -> str:
    return datetime.date.fromisoformat(value).strftime("%d %b %Y")


def _validity_lines(data: dict) -> List[str]:
    return [
        "Source: India Meteorological Department (IMD Agromet)",
        f"Issued: {_display_date(data['date'])}",
        f"Valid until: {_display_date(data['valid_until'])}, 11:59 PM IST",
    ]


def _availability(districts: str, data: dict, rows: List[dict]) -> str:
    crops = sorted({r["crop"] for r in rows if r.get("crop")})
    lines = [f"A current IMD agromet advisory is available for {districts}."]
    if crops:
        lines.append(f"Crops covered: {', '.join(crops)}")
    return "\n".join(lines + _validity_lines(data))


def _format_advisory(districts: str, data: dict, rows: List[dict], note: str, show_regional: bool) -> str:
    lines = [f"IMD District Agromet Advisory — {districts}", note]
    for r in rows:
        label = r.get("crop") or "General"
        lines.append(f"[{label}] {r.get('advisory_eng') or ''}")
        regional = r.get("advisory_reg") or ""
        if show_regional and regional:
            lines.append(f"[{label} — regional language text] {regional}")
    return "\n".join([line for line in lines if line] + _validity_lines(data))


async def imd_agromet_advisory(
    ctx: RunContext[FarmerContext],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    crop: Optional[str] = None,
    check_only: bool = False,
) -> str:
    """Get the current IMD district Agromet Advisory (issued every Tuesday and Friday) for the farmer's location.

    Use when the farmer wants the agro advisory / agromet advisory issued by IMD for their district,
    or agrees to receive it. With check_only=True it only says whether a current advisory exists
    (use this alongside a weather or crop answer before offering the advisory).
    IMPORTANT: if the farmer names a place (village, district, city), you MUST first call forward_geocode
    for that place and pass its latitude/longitude here. Leave latitude/longitude empty ONLY when no place
    is named; the farmer's browser location is then used.

    Args:
        latitude: Latitude of the farm/location (optional if browser coordinates are available).
        longitude: Longitude of the farm/location (optional if browser coordinates are available).
        crop: Crop name in English (e.g. "Rice", "Maize", "Cotton") to show only that crop's advisory.
        check_only: Only check whether a current advisory exists, without the advisory text.

    Returns:
        str: The advisory texts with source, issue date and valid-until date, or a message explaining what is missing.
    """
    lat = latitude if latitude is not None else ctx.deps.latitude
    lon = longitude if longitude is not None else ctx.deps.longitude
    if lat is None or lon is None:
        return ("Location not available. Ask the farmer to share their location, or their village/district name "
                "(then use forward_geocode and call this tool with the coordinates).")

    if not resolver_configured() or not IMD_DB_URL:
        logger.error("IMD advisory tool is not configured (RESOLVER_URL, RESOLVER_CALLBACK_URL, IMD_DB_URL)")
        return "The IMD advisory service is not available right now. Please try again later."

    try:
        keys = await _location_keys(lat, lon)
    except httpx.HTTPError as exc:
        logger.error(f"Resolver request failed: {exc}")
        return "The location service is not reachable right now. Please try again later."
    if not keys:
        return "Could not identify the district for this location. Ask the farmer for their district name."

    try:
        data = await asyncio.to_thread(_lookup_sync, keys)
    except (psycopg2.Error, RuntimeError) as exc:
        logger.error(f"IMD advisory DB lookup failed: {exc}")
        return "The IMD advisory service is not reachable right now. Please try again later."

    districts = ", ".join(d.get("name") or str(d.get("lgd_district_code")) for d in data.get("districts", []))
    if not districts:
        logger.warning(f"IMD advisory: no district mapped for location {lat:.3f},{lon:.3f}")
        return "Could not identify the district for this location. Ask the farmer for their district name."
    rows = data.get("advisories", [])
    if not rows:
        return (f"There is no current IMD agromet advisory for {districts}. IMD issues a new one every Tuesday "
                "and Friday; an expired advisory must not be shown.")
    if check_only:
        return _availability(districts, data, rows)

    rows, note = _filter_by_crop(rows, crop, districts)
    show_regional = (ctx.deps.lang_code or "en") != "en"
    return _format_advisory(districts, data, rows, note, show_regional)
