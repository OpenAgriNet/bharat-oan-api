"""
IMD district agromet advisory tool: farmer location -> resolver keys -> imd_agromet DB.
"""
import asyncio
import datetime
import os
import re
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

# One IMD mention per conversation: set when a note is added or the advisory is shared.
MENTIONED_NAMESPACE = "imd-agromet-mentioned"
MENTIONED_TTL_SECONDS = 24 * 3600


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


def _coords(ctx: RunContext[FarmerContext], latitude: Optional[float], longitude: Optional[float]):
    """The given point, or the farmer's browser location when none is given."""
    lat = latitude if latitude is not None else ctx.deps.latitude
    lon = longitude if longitude is not None else ctx.deps.longitude
    return lat, lon


def _district_names(data: dict) -> str:
    return ", ".join(d.get("name") or str(d.get("lgd_district_code")) for d in data.get("districts", []))


def _display_date(value: str) -> str:
    return datetime.date.fromisoformat(value).strftime("%d %b %Y")


def _validity_block(data: dict) -> str:
    return "\n".join([
        "End the answer with these three lines as a separate block, translated but otherwise unchanged. "
        "Do not mix these dates into the advice, and write nothing after them: no question, offer or "
        "mention of other crops.",
        "Source: India Meteorological Department (IMD Agromet)",
        f"Issued: {_display_date(data['date'])}",
        f"Valid until: {_display_date(data['valid_until'])}, 11:59 PM IST",
    ])


def _available_crops(districts: str, rows: List[dict], note: str = "") -> str:
    crops = sorted({r["crop"] for r in rows if r.get("crop")})
    lines = [note, f"A current IMD agromet advisory is available for {districts}."]
    if crops:
        lines.append(f"Crops covered: {', '.join(crops)}")
    lines.append("Do not give any advice yet. Tell the farmer which crops it covers and ask which one they want, "
                 "then call this tool again with that crop.")
    return "\n".join(line for line in lines if line)


def _format_advisory(districts: str, data: dict, rows: List[dict], show_regional: bool) -> str:
    lines = [f"IMD District Agromet Advisory — {districts}",
             "Present every item below as given: you may translate and rephrase, but do not add, drop, soften "
             "or merge any recommendation. Give only this crop's advisory: do not ask about or offer it for "
             "other crops."]
    for r in rows:
        label = r.get("crop") or "General"
        lines.append(f"[{label}] {r.get('advisory_eng') or ''}")
        regional = r.get("advisory_reg") or ""
        if show_regional and regional:
            lines.append(f"[{label} — regional language text] {regional}")
    return "\n".join(lines + [_validity_block(data)])


async def imd_agromet_advisory(
    ctx: RunContext[FarmerContext],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    crop: Optional[str] = None,
) -> str:
    """Get the current IMD district Agromet Advisory (issued every Tuesday and Friday) for the farmer's location.

    Use only when the farmer asks for the agro advisory / agromet advisory issued by IMD for their district,
    or chooses a crop after being told it is available. Without a crop it lists the crops the current
    advisory covers so the farmer can choose one.
    IMPORTANT: if the farmer names a place (village, district, city), you MUST first call forward_geocode
    for that place and pass its latitude/longitude here. Leave latitude/longitude empty ONLY when no place
    is named; the farmer's browser location is then used.

    Args:
        latitude: Latitude of the farm/location (optional if browser coordinates are available).
        longitude: Longitude of the farm/location (optional if browser coordinates are available).
        crop: Crop name in English (e.g. "Rice", "Maize", "Cotton") for that crop's advisory.
            Leave empty to only check what is available and which crops it covers.

    Returns:
        str: The advisory texts with source, issue date and valid-until date, or a message explaining what is missing.
    """
    lat, lon = _coords(ctx, latitude, longitude)
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

    districts = _district_names(data)
    if not districts:
        logger.warning(f"IMD advisory: no district mapped for location {lat:.3f},{lon:.3f}")
        return "Could not identify the district for this location. Ask the farmer for their district name."
    rows = data.get("advisories", [])
    if not rows:
        return (f"There is no current IMD agromet advisory for {districts}. IMD issues a new one every Tuesday "
                "and Friday; an expired advisory must not be shown.")
    if not crop:
        return _available_crops(districts, rows)

    crop_rows = [r for r in rows if (r.get("crop") or "").lower() == crop.strip().lower()]
    if not crop_rows:
        logger.warning(f"IMD advisory: crop {crop!r} not matched in the bulletin for {districts}")
        return _available_crops(districts, rows, f"The current bulletin has no advisory for {crop}.")
    show_regional = (ctx.deps.lang_code or "en") != "en"
    await cache.set(ctx.deps.session_id, True, ttl=MENTIONED_TTL_SECONDS, namespace=MENTIONED_NAMESPACE)
    return _format_advisory(districts, data, crop_rows, show_regional)


async def agromet_note(
    ctx: RunContext[FarmerContext],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    query: Optional[str] = None,
) -> str:
    """Line to append to a weather result (no query) or a crop-advice search result (its English query).

    Returns a note only when a current IMD advisory exists for the location (and, for a crop search, covers
    a crop named in the query) and the advisory has not been mentioned or shared in this conversation yet.
    Returns an empty string otherwise, including on any lookup failure, so the calling tool is never affected.
    """
    lat, lon = _coords(ctx, latitude, longitude)
    if "imd_agromet_advisory" not in ctx.deps.capabilities.policy.allowed_tools:
        return ""
    if lat is None or lon is None or not resolver_configured() or not IMD_DB_URL:
        return ""
    try:
        if await cache.get(ctx.deps.session_id, namespace=MENTIONED_NAMESPACE):
            return ""
        keys = await _location_keys(lat, lon)
        if not keys:
            return ""
        data = await asyncio.to_thread(_lookup_sync, keys)
    except Exception as exc:  # best effort: a failed check must not break the weather or search answer
        logger.warning(f"IMD advisory check skipped: {exc}")
        return ""

    districts = _district_names(data)
    crops = sorted({r["crop"] for r in data.get("advisories", []) if r.get("crop")})
    if not districts or not crops:
        return ""
    if query is None:
        line = (f"End the answer with one line saying that IMD has a current agromet advisory for {districts} "
                f"covering {', '.join(crops)}, and asking which crop the farmer wants it for.")
    else:
        matched = [c for c in crops if re.search(rf"\b{re.escape(c)}s?\b", query, re.IGNORECASE)]
        if not matched:
            return ""
        line = f"End the answer with one line asking if the farmer would also like the IMD agromet advisory for {matched[0]}."
    try:
        await cache.set(ctx.deps.session_id, True, ttl=MENTIONED_TTL_SECONDS, namespace=MENTIONED_NAMESPACE)
    except Exception as exc:  # the note is still correct; only the once-per-conversation guard is lost
        logger.warning(f"IMD advisory mention flag not saved: {exc}")
    return f"\n\n[IMD agromet advisory note] {line} Do not add anything else about it."
