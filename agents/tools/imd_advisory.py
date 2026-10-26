"""
IMD district agromet advisory tool: farmer location -> resolver keys -> imd_agromet DB.
"""
import asyncio
import datetime
import os
import re
import threading
import unicodedata
import uuid
from typing import List, Optional, Tuple

import httpx
import psycopg2
from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool
from pydantic_ai.tools import RunContext
from rapidfuzz import fuzz

from agents.deps import FarmerContext
from agents.tools.chat_place import is_state_point, named_place, remember_place
from agents.tools.maps import locate
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

# Script of each chat language (unicodedata name prefix); several languages share Devanagari and Bengali.
SCRIPTS = {"as": "BENGALI", "bn": "BENGALI", "gu": "GUJARATI", "hi": "DEVANAGARI", "kn": "KANNADA",
           "mai": "DEVANAGARI", "ml": "MALAYALAM", "mr": "DEVANAGARI", "or": "ORIYA", "pa": "GURMUKHI",
           "ta": "TAMIL", "te": "TELUGU"}
CROP_MATCH_SCORE = 90  # small spelling differences, e.g. "soybean" vs IMD's "Soyabean"

# Words for a whole IMD category, mapped to IMD's own category names (normalised; "Fishieries" is IMD's
# spelling). IMD names items specifically ("Common fish", "Leghorn"), while farmers ask for "fish" or "poultry".
LIVESTOCK = ("live stock",)
FISH = ("fishieries", "fisheries")
POULTRY = ("poultry",)
BEES = ("apiculture",)
SILK = ("sericulture",)
ANIMALS = LIVESTOCK + FISH + POULTRY + BEES + SILK
CATEGORY_WORDS = {
    "animal": ANIMALS, "animals": ANIMALS, "livestock": ANIMALS,
    "cattle": LIVESTOCK, "dairy": LIVESTOCK,
    "fish": FISH, "fishes": FISH, "fishery": FISH, "fisheries": FISH,
    "poultry": POULTRY,
    "bee": BEES, "bees": BEES, "beekeeping": BEES, "apiculture": BEES,
    "silk": SILK, "silkworm": SILK, "silkworms": SILK, "sericulture": SILK,
}

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


async def _coords(ctx: RunContext[FarmerContext], latitude: Optional[float], longitude: Optional[float]):
    """The given point, else the last place the farmer named in this chat, else the browser location."""
    if latitude is not None and longitude is not None:
        return latitude, longitude
    return await named_place(ctx.deps.session_id) or (ctx.deps.latitude, ctx.deps.longitude)


def _district_names(data: dict) -> str:
    return ", ".join(d.get("name") or str(d.get("lgd_district_code")) for d in data.get("districts", []))


def _display_date(value: str) -> str:
    return datetime.date.fromisoformat(value).strftime("%d %b %Y")


def _source_line(data: dict) -> str:
    """BV's one-line source citation, carrying the bulletin's dates so they are never dropped."""
    return (f"Source: India Meteorological Department (IMD Agromet) · Issued {_display_date(data['date'])} · "
            f"Valid until {_display_date(data['valid_until'])}, 11:59 PM IST")


def _reply(data_lines: List[str], how: str) -> str:
    """Tool result with the facts and the answering instructions kept apart, so instructions are not echoed."""
    return "\n".join(["[IMD agromet advisory]", *[line for line in data_lines if line], "",
                      f"[How to answer — instructions for you, never repeat them to the farmer] {how}"])


CITE_SOURCE = ("Cite the Source line above in bold as the source citation, translated but keeping its Issued and "
               "Valid until dates.")


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def _names(row: dict) -> List[str]:
    return [_normalise(n) for n in [row.get("crop") or "", *(row.get("aliases") or [])] if n]


def _is_crop(row: dict, crop: str) -> bool:
    """The farmer's crop is this row's crop or one of its aliases, allowing small spelling differences."""
    wanted = _normalise(crop)
    return any(fuzz.ratio(wanted, name) >= CROP_MATCH_SCORE for name in _names(row))


def _mentioned_in(row: dict, query: str) -> bool:
    """A crop-advice search query names this row's crop (word match, allowing small spelling differences)."""
    words = _normalise(query).split()
    for name in _names(row):
        size = len(name.split())
        phrases = [" ".join(words[i:i + size]) for i in range(len(words) - size + 1)]
        if any(fuzz.ratio(phrase, name) >= CROP_MATCH_SCORE or phrase == f"{name}s" for phrase in phrases):
            return True
    return False


def _in_category_named(rows: List[dict], text: str) -> List[dict]:
    """Rows of the IMD categories a group word in the text stands for, e.g. "fish" -> Fishieries."""
    categories = {c for word in _normalise(text).split() for c in CATEGORY_WORDS.get(word, ())}
    return [r for r in rows if r.get("crop") and _normalise(r.get("category") or "") in categories]


def _by_category(rows: List[dict]) -> dict:
    """{IMD category: sorted item names}, in bulletin order of the categories."""
    groups: dict = {}
    for r in rows:
        if r.get("crop"):
            names = groups.setdefault(r.get("category") or "Other", [])
            if r["crop"] not in names:
                names.append(r["crop"])
    return {category: sorted(names) for category, names in groups.items()}


def _covered(rows: List[dict]) -> str:
    """'Crop (Maize, Onion), Live Stock (Cow)' using IMD's category names."""
    return ", ".join(f"{category} ({', '.join(names)})" for category, names in _by_category(rows).items())


def _script(text: str) -> Optional[str]:
    """Script of the first non-Latin letter, e.g. "KANNADA"; None for English or empty text."""
    letter = next((ch for ch in text if ord(ch) > 127 and ch.isalpha()), None)
    return unicodedata.name(letter).split(" ")[0] if letter else None


def _regional_text(row: dict, lang: str) -> str:
    """IMD's regional text only when it is in the farmer's language; otherwise the English text is used.

    The state's language from the IMD language master decides; when it is missing, English or does not match
    the text's script, the script alone decides if it belongs to one chat language only.
    """
    text = row.get("advisory_reg") or ""
    script = _script(text)
    if not script or SCRIPTS.get(lang) != script:
        return ""
    state_lang = row.get("reg_lang_code")
    if SCRIPTS.get(state_lang) == script:
        return text if state_lang == lang else ""
    return text if [code for code, s in SCRIPTS.items() if s == script] == [lang] else ""


def _available_crops(districts: str, data: dict, rows: List[dict], missing: Optional[str] = None) -> str:
    data_lines = [f"This bulletin has no advisory for {missing}." if missing else "",
                  f"A current IMD agromet advisory is available for {districts}. It covers:",
                  *(f"- {category}: {', '.join(names)}" for category, names in _by_category(rows).items()),
                  _source_line(data)]
    how = ((f"Start by saying this bulletin has no advisory for {missing}. " if missing else "")
           + "Say in one sentence that the advisory is available, then show the list exactly as above: one line per "
           "category with every item, the category and item names in the farmer's language. " + CITE_SOURCE
           + " Your follow-up question asks which one they want; when they choose, call this tool again with it.")
    return _reply(data_lines, how)


def _format_advisory(districts: str, data: dict, rows: List[dict], lang: str, others: List[dict],
                     missing: Optional[str] = None) -> str:
    data_lines = [f"This bulletin has no advisory for {missing}." if missing else "",
                  f"IMD District Agromet Advisory for {districts}:"]
    for r in rows:
        label = " — ".join(part for part in [r.get("crop") or "General", r.get("crop_stage_name")] if part)
        data_lines.append(f"[{label}] {r.get('advisory_eng') or ''}")
        regional = _regional_text(r, lang)
        if regional:
            data_lines.append(f"[{label} — IMD text in the farmer's language] {regional}")
    data_lines.append(_source_line(data))
    offer = (f"Your follow-up question may offer one other item from this bulletin: {_covered(others)}."
             if _by_category(others) else "Your follow-up question offers nothing else from this advisory.")
    how = ((f"Start by saying this bulletin has no advisory for {missing}. " if missing else "")
           + "Give only this advice, item by item, starting each item with the crop stage shown in its label. "
           "Translate and simplify the wording, but keep every recommendation: every step, quantity, product, named "
           "institution or implement, and time-critical wording such as a last date or last sowing window. "
           + CITE_SOURCE + " " + offer)
    return _reply(data_lines, how)


async def imd_agromet_advisory(
    ctx: RunContext[FarmerContext],
    place: Optional[str] = None,
    crop: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> str:
    """Get the current IMD district Agromet Advisory (issued every Tuesday and Friday) for the farmer's location.

    Use only when the farmer asks for the agro advisory / agromet advisory issued by IMD for their district,
    or chooses a crop after being told it is available. Without a crop it lists the crops the current
    advisory covers so the farmer can choose one.
    If the farmer names a place, pass its name as `place`; do not call forward_geocode for it. With no place
    and no coordinates, the last place the farmer named in this chat is used, otherwise the browser location.

    Args:
        place: The village, district or city the farmer named, in English (e.g. "Srinagar, Jammu and Kashmir").
        crop: Crop or animal name in English (e.g. "Rice", "Maize", "Cow") for that advisory, or a group in
            English ("Animals", "Fish", "Poultry", "Bees", "Silk") to list what the advisory covers in that group.
            For several items, pass them together, e.g. "Goat, Chick", to get the advice for each.
            Leave empty to list everything the current advisory covers.
        latitude: Latitude, only when the farmer gave coordinates and no place name.
        longitude: Longitude, only when the farmer gave coordinates and no place name.

    Returns:
        str: The advisory texts with source, issue date and valid-until date, or a message explaining what is missing.
    """
    try:
        lat, lon = await _point(ctx, place, latitude, longitude)
        data = await _bulletin(lat, lon)
    except _Stop as stop:
        return str(stop)
    return await _answer(ctx, data, crop)


class _Stop(Exception):
    """Raised with the reply for the model when the lookup cannot go on."""


async def _point(ctx: RunContext[FarmerContext], place: Optional[str], latitude: Optional[float],
                 longitude: Optional[float]) -> Tuple[float, float]:
    """The named place, else the given coordinates, the last named place or the browser location."""
    if place:
        try:
            found = await locate(place)
        except httpx.HTTPError as exc:
            logger.error(f"Geocoding {place!r} failed: {exc}")
            raise _Stop("The location service is not reachable right now. Please try again later.") from exc
        if not found or found[0].latitude is None:
            raise _Stop(f"No place called '{place}' was found. Ask the farmer for the district name.")
        location, kind = found
        await remember_place(ctx.deps.session_id, location.latitude, location.longitude, kind)
        if kind == "state":
            raise _Stop(STATE_ONLY)
        return location.latitude, location.longitude
    lat, lon = await _coords(ctx, latitude, longitude)
    if lat is None or lon is None:
        raise _Stop("Location not available. Ask the farmer to share their location, or their village or "
                    "district name and call this tool with it as `place`.")
    if await is_state_point(ctx.deps.session_id, lat, lon):
        raise _Stop(STATE_ONLY)
    return lat, lon


async def _bulletin(lat: float, lon: float) -> dict:
    """imd_lookup() result for the district at the point."""
    if not resolver_configured() or not IMD_DB_URL:
        logger.error("IMD advisory tool is not configured (RESOLVER_URL, RESOLVER_CALLBACK_URL, IMD_DB_URL)")
        raise _Stop("The IMD advisory service is not available right now. Please try again later.")
    try:
        keys = await _location_keys(lat, lon)
    except httpx.HTTPError as exc:
        logger.error(f"Resolver request failed: {exc}")
        raise _Stop("The location service is not reachable right now. Please try again later.") from exc
    if not keys:
        raise _Stop("Could not identify the district for this location. Ask the farmer for their district name.")
    try:
        data = await asyncio.to_thread(_lookup_sync, keys)
    except (psycopg2.Error, RuntimeError) as exc:
        logger.error(f"IMD advisory DB lookup failed: {exc}")
        raise _Stop("The IMD advisory service is not reachable right now. Please try again later.") from exc
    if not _district_names(data):
        logger.warning(f"IMD advisory: no district mapped for location {lat:.3f},{lon:.3f}")
        raise _Stop("Could not identify the district for this location. Ask the farmer for their district name.")
    return data


async def _answer(ctx: RunContext[FarmerContext], data: dict, crop: Optional[str]) -> str:
    """The list, the chosen item's advice, or why there is none."""
    districts = _district_names(data)
    rows = data.get("advisories", [])
    if not rows:
        return _reply([f"There is no current IMD agromet advisory for {districts}.",
                       "IMD issues new advisories every Tuesday and Friday."],
                      "Tell the farmer both facts in their language. Give no Source line, and do not offer to check "
                      "again later.")
    if not crop:
        return _available_crops(districts, data, rows)

    chosen, missing, choose = _select(rows, crop)
    if not chosen:
        logger.warning(f"IMD advisory: crop {crop!r} not matched in the bulletin for {districts}")
        return _available_crops(districts, data, rows, missing=crop)
    not_found = ", ".join(missing) or None
    if choose:  # a group such as "fish": let the farmer choose
        return _available_crops(districts, data, chosen, missing=not_found)
    await cache.set(ctx.deps.session_id, True, ttl=MENTIONED_TTL_SECONDS, namespace=MENTIONED_NAMESPACE)
    others = [r for r in rows if r not in chosen]
    return _format_advisory(districts, data, chosen, ctx.deps.lang_code or "en", others, not_found)


def _requested(rows: List[dict], crop: str) -> List[str]:
    """The item names asked for: "Goat and Chick" -> Goat, Chick; a name that is itself an item is kept whole."""
    if any(_is_crop(r, crop) for r in rows) or _in_category_named(rows, crop):
        return [crop]
    return [name.strip() for name in re.split(r",|/|&|\+|\band\b", crop, flags=re.IGNORECASE) if name.strip()]


def _select(rows: List[dict], crop: str) -> Tuple[List[dict], List[str], bool]:
    """Rows for the items asked for, the names not in this bulletin, and whether a group needs a choice first."""
    chosen: List[dict] = []
    missing: List[str] = []
    choose = False
    for name in _requested(rows, crop):
        found = [r for r in rows if _is_crop(r, name)]
        if not found:
            found = _in_category_named(rows, name)
            choose = choose or len({r["crop"] for r in found}) > 1
        if not found:
            missing.append(name)
        chosen += [r for r in found if r not in chosen]
    return chosen, missing, choose


STATE_ONLY = _reply(["IMD agromet advisories are issued per district, not for a whole state."],
                    "Tell the farmer this; it does not mean there is no advisory. Give no Source line. Your one "
                    "follow-up question asks which district they want; then check that district.")
NO_MENTION = "\n\n[IMD agromet advisory note] Do not mention or offer the IMD agromet advisory in this answer."


async def _offer_line(ctx: RunContext[FarmerContext], lat: float, lon: float, query: Optional[str]) -> Optional[str]:
    """The one-line offer when a current advisory exists for the point (and covers the queried crop), else None."""
    if await cache.get(ctx.deps.session_id, namespace=MENTIONED_NAMESPACE):
        return None
    keys = await _location_keys(lat, lon)
    if not keys:
        return None
    data = await asyncio.to_thread(_lookup_sync, keys)
    districts = _district_names(data)
    rows = [r for r in data.get("advisories", []) if r.get("crop")]
    if not districts or not rows:
        return None
    valid = f"valid until {datetime.date.fromisoformat(data['valid_until']).strftime('%d %b')}"
    if query is None:
        return (f"End the answer with one line saying that IMD has a current agromet advisory for {districts} "
                f"covering {_covered(rows)} ({valid}), and asking which one the farmer wants it for.")
    matched = next((r["crop"] for r in rows if _mentioned_in(r, query)), None) or _covered(
        _in_category_named(rows, query))
    if not matched:
        return None
    return (f"End the answer with one line asking if the farmer would also like the IMD agromet advisory "
            f"for {matched} ({valid}).")


async def agromet_note(
    ctx: RunContext[FarmerContext],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    query: Optional[str] = None,
) -> str:
    """Note to append to a weather result (no query) or a crop-advice search result (its English query).

    When a current IMD advisory exists for the location (and, for a crop search, covers a crop named in the query)
    and it has not been mentioned or shared in this conversation yet, the note tells the model to offer it in one
    line. In every other case, including any lookup failure, the note tells the model not to mention it, so the
    model never offers an advisory that does not exist. Empty only when the IMD tool is not enabled.
    """
    if "imd_agromet_advisory" not in ctx.deps.capabilities.policy.allowed_tools:
        return ""
    lat, lon = await _coords(ctx, latitude, longitude)
    if lat is None or lon is None or not resolver_configured() or not IMD_DB_URL:
        return NO_MENTION
    if await is_state_point(ctx.deps.session_id, lat, lon):
        return NO_MENTION
    try:
        line = await _offer_line(ctx, lat, lon, query)
    except Exception as exc:  # best effort: a failed check must not break the weather or search answer
        logger.warning(f"IMD advisory check skipped: {exc}")
        return NO_MENTION
    if not line:
        return NO_MENTION
    try:
        await cache.set(ctx.deps.session_id, True, ttl=MENTIONED_TTL_SECONDS, namespace=MENTIONED_NAMESPACE)
    except Exception as exc:  # the note is still correct; only the once-per-conversation guard is lost
        logger.warning(f"IMD advisory mention flag not saved: {exc}")
    return f"\n\n[IMD agromet advisory note] {line} Do not add anything else about it."
