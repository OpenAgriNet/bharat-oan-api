"""
AgriStack farmer data for farmers who logged in with AgriStack and gave consent:
- Category A (identity + land basics, farmerId only): location for weather questions.
- Category B (land + crop survey, farmerId/season/year): crops for mandi, crop advisory
  and pest questions.

Flow: the UI sends the chat session id to AgriStack as ``session_id``; AgriStack posts
its callback to ``/api/callback`` (app/routers/callback.py), which caches the payload in
Redis under that session id. This module reads that cached callback to find the farmer
ID and consent, then asks the provider backend (``provider.id = agristack-agri``) for
Category A or B data over the Beckn network, the same way the SATHI seed tool does.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import pytz
from langfuse import observe
from pydantic_ai.tools import RunContext, ToolDefinition

from agents.deps import FarmerContext
from app.config import get_default_httpx_timeout
from app.core.cache import cache
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

logger = get_logger(__name__)

# Written by app/routers/callback.py (not imported: that would pull in the routers package).
CALLBACK_STATUS_NAMESPACE = "callback-status"
PROFILE_CACHE_NAMESPACE = "agristack-profile"
PROFILE_CACHE_TTL_SECONDS = 60 * 60

CONSENT_GIVEN = "CONTINUE WITH CONSENT"

# Farmer-identifying fields never passed to the model.
_SENSITIVE_KEY = re.compile(
    r"aadhaar|aadhar|mobile|phone|email|hash|dob|birth|account|ifsc|^pan$|pan_?(no|num|number|card)", re.I
)
_TAG_SECTIONS = {
    "farmer-data": "farmer",
    "land-data": "land",
    "crop-survey-data": "crop_survey",
    "land-ownership-data": "land_ownership",
}
_MAX_SECTION_CHARS = 4000


# -----------------------
# Session -> farmer link
# -----------------------
async def get_agristack_link(session_id: str) -> Optional[dict[str, Any]]:
    """Farmer ID and consent for this chat session, from the cached AgriStack callback.

    Returns ``{"farmer_id": str, "has_consent": bool}`` or None when the session has no
    AgriStack login (or Redis is unavailable).
    """
    if not session_id:
        return None
    try:
        state = await cache.get(session_id, namespace=CALLBACK_STATUS_NAMESPACE)
    except Exception:
        logger.exception("agristack.link_lookup_failed session=%s", session_id)
        return None
    if not isinstance(state, dict):
        return None

    body = state.get("body") if isinstance(state.get("body"), dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    farmer_data = data.get("farmerData") if isinstance(data.get("farmerData"), dict) else {}
    farmer_id = farmer_data.get("centralId") or data.get("farmerId") or body.get("farmerId")
    if not farmer_id:
        return None

    consent_name = str(body.get("consentName") or "").strip().upper()
    return {"farmer_id": str(farmer_id), "has_consent": consent_name == CONSENT_GIVEN}


# -----------------------
# Category A / B request
# -----------------------
CATEGORY_A = "agristack-category-a"
CATEGORY_B = "agristack-category-b"

def current_season_and_year(now: Optional[datetime] = None) -> tuple[str, str]:
    """Season name and its starting year, overridable via AGRISTACK_SEASON / AGRISTACK_YEAR.

    Kharif Jun-Sep, Rabi Oct-Feb (Jan/Feb belong to the Rabi that started the previous
    year), Zaid Mar-May.
    """
    now = now or datetime.now(pytz.timezone("Asia/Kolkata"))
    month = now.month
    if 6 <= month <= 9:
        season, year = "Kharif", now.year
    elif month >= 10:
        season, year = "Rabi", now.year
    elif month <= 2:
        season, year = "Rabi", now.year - 1
    else:
        season, year = "Zaid", now.year
    return os.getenv("AGRISTACK_SEASON") or season, os.getenv("AGRISTACK_YEAR") or str(year)


def build_payload(
    farmer_id: str, category: str, session_id: str = "", question_id: str = ""
) -> dict[str, Any]:
    """Beckn search for the provider's AgristackService. Category B also needs season/year."""
    tags = [{"descriptor": {"code": "farmerId"}, "value": farmer_id}]
    if category == CATEGORY_B:
        season, year = current_season_and_year()
        tags += [
            {"descriptor": {"code": "season"}, "value": season},
            {"descriptor": {"code": "year"}, "value": year},
        ]
    now = datetime.now(timezone.utc)
    return {
        "context": {
            "domain": "schemes:vistaar",
            "action": "search",
            "version": "1.1.0",
            "bap_id": os.getenv("BAP_ID"),
            "bap_uri": os.getenv("BAP_URI"),
            "bpp_id": os.getenv("BPP_ID"),
            "bpp_uri": os.getenv("BPP_URI"),
            "transaction_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "ttl": "PT10M",
            "location": {"country": {"code": "IND"}, "city": {"code": "*"}},
            "tags": {"session_id": session_id, "question_id": question_id},
        },
        "message": {
            "intent": {
                "provider": {"id": "agristack-agri"},
                "item": {"id": category, "tags": tags},
            }
        },
    }


def _strip_sensitive(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_sensitive(v) for k, v in node.items() if not _SENSITIVE_KEY.search(str(k))}
    if isinstance(node, list):
        return [_strip_sensitive(v) for v in node]
    return node


def parse_agristack_response(data: Any) -> tuple[dict[str, Any], Optional[str]]:
    """Collects farmer/land/crop-survey sections from the on_search catalog.

    Returns ``(sections, error_message)``; sections are keyed farmer/land/crop_survey/
    land_ownership with sensitive fields removed.
    """
    sections: dict[str, Any] = {}
    error: Optional[str] = None
    responses = data.get("responses") if isinstance(data, dict) else None
    if not isinstance(responses, list):
        # Some BAPs return a single on_search body instead of a responses list.
        responses = [data] if isinstance(data, dict) else []

    for resp in responses:
        catalog = ((resp or {}).get("message") or {}).get("catalog") or {}
        for provider in catalog.get("providers") or []:
            for item in (provider or {}).get("items") or []:
                if (item or {}).get("id") == "error":
                    error = ((item.get("descriptor") or {}).get("short_desc")) or "AgriStack request failed"
                    continue
                for tag in (item or {}).get("tags") or []:
                    key = _TAG_SECTIONS.get(((tag or {}).get("descriptor") or {}).get("code"))
                    if not key or key in sections:
                        continue
                    raw = next((x.get("value") for x in tag.get("list") or [] if isinstance(x, dict)), None)
                    try:
                        value = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        value = raw
                    if value not in (None, [], {}):
                        sections[key] = _strip_sensitive(value)
    return sections, (None if sections else error)


async def fetch_agristack(
    farmer_id: str, category: str, session_id: str = "", question_id: str = ""
) -> tuple[dict[str, Any], Optional[str]]:
    """Category A or B sections for a farmer, cached per session for PROFILE_CACHE_TTL_SECONDS."""
    cache_key = f"{session_id}:{farmer_id}:{category}"
    try:
        cached = await cache.get(cache_key, namespace=PROFILE_CACHE_NAMESPACE)
        if isinstance(cached, dict) and cached:
            return cached, None
    except Exception:
        logger.warning("agristack.profile_cache_read_failed session=%s", session_id)

    bap_endpoint = os.getenv("BAP_ENDPOINT")
    if not bap_endpoint:
        return {}, "AgriStack service is not configured (BAP_ENDPOINT missing)."
    bep = bap_endpoint.rstrip("/")
    search_url = bep if bep.endswith("/search") else bep + "/search"

    payload = build_payload(farmer_id, category, session_id, question_id)
    lf_update_current_observation(
        metadata={
            "tool": f"agristack.{category}",
            "transaction_id": payload["context"]["transaction_id"],
        }
    )
    async with httpx.AsyncClient(timeout=get_default_httpx_timeout()) as client:
        response = await client.post(search_url, json=payload)
    if response.status_code != 200:
        logger.error("AgriStack %s status %s — %s", category, response.status_code, (response.text or "")[:500])
        return {}, "AgriStack service returned an error."

    sections, error = parse_agristack_response(response.json())
    if sections:
        try:
            await cache.set(cache_key, sections, ttl=PROFILE_CACHE_TTL_SECONDS, namespace=PROFILE_CACHE_NAMESPACE)
        except Exception:
            logger.warning("agristack.profile_cache_write_failed session=%s", session_id)
    return sections, error


def _section_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_SECTION_CHARS else text[:_MAX_SECTION_CHARS] + " …(truncated)"


# -----------------------
# Tools
# -----------------------
_ASK_FARMER = "Ask the farmer for the details you need."


async def only_with_agristack_consent(
    ctx: RunContext[FarmerContext], tool_def: ToolDefinition
) -> Optional[ToolDefinition]:
    """Tool ``prepare`` hook: offer AgriStack tools only when the farmer logged in with consent."""
    return tool_def if ctx.deps.agristack_status == "consent" else None
_GEOCODE_HINT = (
    "Use the village/district/state from the land parcels (or farmer details) with forward_geocode "
    "to get coordinates. Do not show IDs, survey numbers or raw data to the farmer. Source: AgriStack"
)


async def _run(ctx: RunContext[FarmerContext], category: str, heading: str) -> str:
    link = await get_agristack_link(ctx.deps.session_id)
    if not link:
        return f"Farmer is not logged in with AgriStack. {_ASK_FARMER}"
    if not link["has_consent"]:
        return f"Farmer logged in with AgriStack without consent to share land and crop data. {_ASK_FARMER}"

    try:
        sections, error = await fetch_agristack(
            link["farmer_id"], category, session_id=ctx.deps.session_id, question_id=ctx.deps.question_id
        )
    except httpx.TimeoutException:
        logger.error("AgriStack %s timed out", category)
        return f"AgriStack request timed out. {_ASK_FARMER}"
    except httpx.RequestError as e:
        logger.error("AgriStack %s request failed: %s", category, e)
        return f"Could not reach AgriStack. {_ASK_FARMER}"
    except Exception:
        logger.exception("AgriStack %s failed", category)
        return f"AgriStack data is unavailable. {_ASK_FARMER}"

    if not sections:
        return f"No AgriStack data found ({error or 'empty response'}). {_ASK_FARMER}"

    lines = [heading]
    for key, label in (
        ("land", "Land parcels"),
        ("crop_survey", "Crop survey"),
        ("land_ownership", "Land ownership"),
        ("farmer", "Farmer details"),
    ):
        if key in sections:
            lines.append(f"{label}: {_section_text(sections[key])}")
    lines.append(_GEOCODE_HINT)
    return "\n".join(lines)


@observe(name="tool:get_agristack_farmer_location", as_type="tool")
async def get_agristack_farmer_location(ctx: RunContext[FarmerContext]) -> str:
    """Location of the logged-in farmer from AgriStack (Category A: identity and land basics).

    Use for weather forecast, weather advisory and weather alert questions where the farmer
    did not name a place — instead of asking. Only works when the farmer logged in with
    AgriStack and gave consent.
    """
    return await _run(ctx, CATEGORY_A, "AgriStack farmer location (use as the farmer's own location):")


@observe(name="tool:get_agristack_farmer_crops", as_type="tool")
async def get_agristack_farmer_crops(ctx: RunContext[FarmerContext]) -> str:
    """Land parcels and crops sown this season for the logged-in farmer, from AgriStack
    (Category B: land and crop survey).

    Use for mandi price, crop advisory and pest/disease questions where the farmer did not
    name a crop or place — instead of asking. Only works when the farmer logged in with
    AgriStack and gave consent.
    """
    return await _run(
        ctx, CATEGORY_B, "AgriStack farmer land and crops (use as the farmer's own location and crops):"
    )
