"""
Crop advisory search through the Beckn network (BAP /search, category knowledge-advisory).

The provider searches advisory chunks that docs-pipeline stores in Qdrant and answers with
an on_search catalog whose items carry `chunk-details` tags: title, crop, section, score,
doc_id, chunk_id, source, text (text already ends with a "Source:" line).
"""
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.search import _catalog_search_context_map, _item_chunk_details_map
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

logger = get_logger(__name__)

KNOWLEDGE_ADVISORY_CATEGORY = "knowledge-advisory"

# Source shown to the farmer for every advisory answer.
ADVISORY_SOURCE = "ICAR - Kisan Sarathi"

# Longest value forwarded for crop / district, and the accepted range for days after sowing.
_MAX_PARAM_LENGTH = 100
_MAX_DAYS_AFTER_SOWING = 730


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_param(value: Optional[str]) -> str:
    """Replace control characters, collapse whitespace, trim, and cap the length."""
    return " ".join(_CONTROL_CHARS.sub(" ", str(value or "")).split())[:_MAX_PARAM_LENGTH].strip()


def _clean_days(value: Optional[int]) -> Optional[int]:
    """A whole number of days in 0..730, else None (the value is then not sent)."""
    try:
        days = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return days if 0 <= days <= _MAX_DAYS_AFTER_SOWING else None


def _search_params_tag(crop: str, district: str, days_after_sowing: Optional[int]) -> List[Dict[str, Any]]:
    """`search_params` tag group the provider reads: crop, district, days_after_sowing."""
    entries = []
    for code, value in (("crop", crop), ("district", district), ("days_after_sowing", days_after_sowing)):
        if value is None or value == "":
            continue
        entries.append({"descriptor": {"code": code}, "value": str(value)})
    if not entries:
        return []
    return [{"descriptor": {"code": "search_params"}, "list": entries}]


def build_advisory_search_payload(
    query: str,
    session_id: str = "",
    question_id: str = "",
    crop: str = "",
    district: str = "",
    days_after_sowing: Optional[int] = None,
) -> Dict[str, Any]:
    """Beckn /search body for advisory document search (knowledge-advisory).

    `crop`, `district` and `days_after_sowing` travel in `item.tags` (group `search_params`);
    the season is decided by the provider, not sent from here.
    """
    now = datetime.now(timezone.utc)
    item: Dict[str, Any] = {"descriptor": {"name": query}}
    tags = _search_params_tag(_clean_param(crop), _clean_param(district), _clean_days(days_after_sowing))
    if tags:
        item["tags"] = tags
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
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "ttl": "PT10M",
            "location": {
                "country": {"code": "IND"},
                "city": {"code": "*"},
            },
            "tags": {
                "session_id": session_id,
                "question_id": question_id,
            },
        },
        "message": {
            "intent": {
                "category": {
                    "descriptor": {
                        "code": KNOWLEDGE_ADVISORY_CATEGORY,
                        "name": KNOWLEDGE_ADVISORY_CATEGORY,
                    }
                },
                "item": item,
            }
        },
    }


def _advisory_result_from_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map one on_search item to an advisory result dict (None when it has no text)."""
    if not isinstance(item, dict):
        return None
    details = _item_chunk_details_map(item)
    desc = item.get("descriptor") if isinstance(item.get("descriptor"), dict) else {}

    text = (details.get("text") or desc.get("long_desc") or "").strip()
    if not text:
        return None
    try:
        score = float(details.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "title": (details.get("title") or desc.get("name") or "").strip(),
        "crop": (details.get("crop") or "").strip(),
        "section": (details.get("section") or "").strip().lower(),
        "score": score,
        "doc_id": (details.get("doc_id") or "").strip(),
        "chunk_id": (details.get("chunk_id") or item.get("id") or "").strip(),
        "source": (details.get("source") or "").strip(),
        "text": text,
    }


def parse_advisory_response(data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Parse the BAP client /search response into (advisory results, search-context map)."""
    responses = data.get("responses") if isinstance(data, dict) else None
    if not isinstance(responses, list) or not responses:
        return [], {}

    fallback_ctx: Dict[str, str] = {}
    for rsp in responses:
        if not isinstance(rsp, dict):
            continue
        message = rsp.get("message") if isinstance(rsp.get("message"), dict) else {}
        catalog = message.get("catalog") if isinstance(message.get("catalog"), dict) else {}
        if not catalog:
            continue

        ctx = _catalog_search_context_map(catalog)
        results: List[Dict[str, Any]] = []
        for prov in catalog.get("providers") or []:
            if not isinstance(prov, dict):
                continue
            for item in prov.get("items") or []:
                parsed = _advisory_result_from_item(item)
                if parsed:
                    results.append(parsed)
        if results:
            return results, ctx
        if not fallback_ctx:
            fallback_ctx = ctx
    return [], fallback_ctx


def format_advisory_results(results: List[Dict[str, Any]], query: str) -> str:
    """Readable block per advisory for the agent; each text already ends with its Source line."""
    blocks = [f"**{r.get('title') or 'Advisory'}**\n```\n{r['text']}\n```" for r in results]
    return "> Advisory Search Results for `" + query + "`\n\n" + "\n\n----\n\n".join(blocks)


def format_search_notes(search_ctx: Dict[str, str], days_after_sowing: Optional[int]) -> str:
    """Short factual notes for the agent about how the search was applied."""
    location = str(search_ctx.get("location_status") or "").strip().lower()
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    days_matched = str(search_ctx.get("days_matched") or "").strip().lower() == "true"
    notes = []
    if location:
        notes.append(f"location_status={location}")
    notes.append(f"advisory_has_day_range={'yes' if day_range else 'no'}")
    if days_after_sowing is not None:
        notes.append(f"days_after_sowing_matched={'yes' if days_matched else 'no'}")
    return "> Search notes: " + "; ".join(notes)


@observe(name="tool:search_advisory", as_type="tool")
async def search_advisory(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: str,
    district: Optional[str] = None,
    days_after_sowing: Optional[int] = None,
    top_k: int = 5,
) -> str:
    """
    Search crop advisories for a farming problem: pests, diseases, symptoms, crop practices
    and harvesting guidance for a specific crop, from ICAR - Kisan Sarathi.

    Use this for crop-advisory questions such as "aphids in wheat" or "when to harvest
    sorghum", only after the farmer has said whether they want generic or personalised
    advice. Do NOT use it for government scheme questions (use search_schemes) or for
    weather, mandi prices or status checks.

    Args:
        query: The farming problem in *English*, 2-5 words, including the crop (required)
        crop: The crop in English, for example "Wheat" or "Rice" (required). Ask the farmer
            when it is not known.
        district: Personalised advice only: the farmer's district (or state if the district
            is unknown). Leave empty for generic advice or when the farmer does not know it.
        days_after_sowing: Personalised advice only: whole number of days since the farmer
            sowed the crop. Leave empty for generic advice or when the farmer does not know it.
        top_k: Maximum number of advisories to return (default: 5)

    Returns:
        Advisory text, then a "Search notes" line, or a message saying nothing was found.
    """
    crop = _clean_param(crop)
    if not crop:
        raise ModelRetry("Ask the farmer which crop they mean, then call search_advisory again with `crop`.")
    district = _clean_param(district)
    days = _clean_days(days_after_sowing)

    payload = build_advisory_search_payload(
        query=query,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
        crop=crop,
        district=district,
        days_after_sowing=days,
    )
    transaction_id = payload["context"]["transaction_id"]
    lf_update_current_observation(
        input={"query": query, "crop": crop, "district": district or None, "days_after_sowing": days, "top_k": top_k},
        metadata={"tool": "advisory.search", "category": KNOWLEDGE_ADVISORY_CATEGORY, "transaction_id": transaction_id},
    )

    bap_endpoint = os.getenv("BAP_ENDPOINT")
    if not bap_endpoint:
        logger.error("BAP_ENDPOINT is not set")
        return "Advisory search service is not configured. Please try again later."
    search_url = bap_endpoint.rstrip("/") + "/search"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(search_url, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error("Advisory search request failed: %s", exc)
        return "Advisory search service is unavailable. Please try again later."

    if response.status_code != 200:
        logger.error("Advisory search returned status %s", response.status_code)
        return "Advisory search service is unavailable. Please try again later."

    try:
        data = response.json()
    except ValueError:
        logger.error("Advisory search returned a non-JSON body")
        return "Advisory search returned an unexpected response. Please try again later."

    results, search_ctx = parse_advisory_response(data)
    status = str(search_ctx.get("status") or "").strip().lower()
    message = str(search_ctx.get("message") or "").strip()
    lf_update_current_observation(
        metadata={
            "tool": "advisory.search",
            "category": KNOWLEDGE_ADVISORY_CATEGORY,
            "transaction_id": transaction_id,
            "network_status": status or None,
            "location_status": search_ctx.get("location_status") or None,
            "has_day_range": search_ctx.get("has_day_range") or None,
            "days_matched": search_ctx.get("days_matched") or None,
            "hit_count": len(results),
            "doc_ids": list(dict.fromkeys(r["doc_id"] for r in results if r["doc_id"])),
            "chunk_ids": [r["chunk_id"] for r in results if r["chunk_id"]],
        }
    )

    if not results:
        if status and status not in ("success", "not_found") and message:
            return message
        return f"No advisory found for `{query}`."
    formatted = format_advisory_results(results[:top_k] if top_k and top_k > 0 else results, query)
    return formatted + "\n\n" + format_search_notes(search_ctx, days)
