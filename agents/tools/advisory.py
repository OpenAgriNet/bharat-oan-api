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
from typing import Any, Dict, List, Literal, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from helpers.ask_first import ASK_FIRST_RETRY, farmer_was_asked
from agents.tools.search import _catalog_search_context_map, _item_chunk_details_map
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

logger = get_logger(__name__)

KNOWLEDGE_ADVISORY_CATEGORY = "knowledge-advisory"

# Source shown to the farmer for every advisory answer.
ADVISORY_SOURCE = "ICAR - Kisan Sarathi"

# Longest value forwarded for crop / season, and the accepted range for days after sowing.
_MAX_PARAM_LENGTH = 100
_MAX_DAYS_AFTER_SOWING = 730
_SEASONS = {"rabi", "kharif", "zaid"}


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




def default_season() -> str:
    """Season configured in env `ADVISORY_DEFAULT_SEASON` (empty when not set)."""
    return _clean_param(os.getenv("ADVISORY_DEFAULT_SEASON"))


def _search_params_tag(crop: str, days_after_sowing: Optional[int], season: str = "") -> List[Dict[str, Any]]:
    """`search_params` tag group the provider reads: crop, days_after_sowing, season (never a location)."""
    entries = []
    for code, value in (
        ("crop", crop),
        ("days_after_sowing", days_after_sowing),
        ("season", season),
    ):
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
    days_after_sowing: Optional[int] = None,
    season: str = "",
) -> Dict[str, Any]:
    """Beckn /search body for advisory document search (knowledge-advisory).

    `crop`, `days_after_sowing` and `season` travel (the location is never sent) in `item.tags` (group `search_params`).
    """
    now = datetime.now(timezone.utc)
    item: Dict[str, Any] = {"descriptor": {"name": query}}
    tags = _search_params_tag(_clean_param(crop), _clean_days(days_after_sowing), _clean_param(season))
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


_TRAILING_SOURCE = re.compile(r"\n+Source:[^\n]*\s*$")


def format_advisory_results(results: List[Dict[str, Any]], query: str) -> str:
    """Readable block per advisory for the agent.

    The provider appends "Source: institute, creator" to each text. That line names the
    institute and a user name, so it is removed here; the answer cites ADVISORY_SOURCE instead.
    """
    blocks = []
    for r in results:
        text = _TRAILING_SOURCE.sub("", r["text"]).rstrip()
        blocks.append(f"**{r.get('title') or 'Advisory'}**\n```\n{text}\n```")
    return (
        "> Advisory Search Results for `" + query + "`\n\n"
        + "\n\n----\n\n".join(blocks)
        + f"\n\n> Cite as (in bold): **Source: {ADVISORY_SOURCE}**"
    )


def format_search_notes(
    search_ctx: Dict[str, str],
    days_after_sowing: Optional[int],
    personalised: bool = False,
    location: str = "",
) -> str:
    """Notes for the agent about how the search was applied, with what to tell the farmer."""
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    days_matched = str(search_ctx.get("days_matched") or "").strip().lower() == "true"
    facts = [f"advisory_has_day_range={'yes' if day_range else 'no'}"]
    if days_after_sowing is not None:
        facts.append(f"days_after_sowing_matched={'yes' if days_matched else 'no'}")
    lines = ["> Search notes: " + "; ".join(facts)]
    if location:
        lines.append(
            f"> The farmer named a location ({location}). Locations are never used to search. Say in one short "
            "sentence that there is no advisory specific to that location, and that this advice is for their "
            "crop, season and sowing days; then give the advice."
        )
    if personalised and days_after_sowing is None:
        lines.append(
            "> The farmer did not give the days since sowing. Say in one short sentence that the advice "
            "is not matched to their sowing stage."
        )
    elif days_after_sowing is not None and (not day_range or not days_matched):
        lines.append(
            f"> The farmer did give the days since sowing ({days_after_sowing}), but this advisory has no guidance "
            "for that sowing stage. Do not say the farmer did not give the days. Say briefly that this is the "
            "general advice for the crop and season."
        )
    elif not day_range:
        lines.append("> This advice is general for the crop and not tied to a sowing stage. Say so briefly.")
    return "\n".join(lines)


_DETAILS_RETRY = (
    "Do not search yet. For personalised advice, ask the farmer in one question for the season (Rabi, Kharif or "
    "Zaid), the days since sowing, their location (district or state) and, if not known yet, the crop, then wait "
    "for the answer. Ask for the location only in this question, never again; it is not used to search. If they "
    "say they do not know the days or do not give a location, search without them."
)


def _require_details_asked(ctx: RunContext[FarmerContext], personalised: bool, days: Optional[int]) -> None:
    """Personalised search with no days: the farmer must have been asked for the details first."""
    if personalised and days is None and not farmer_was_asked(ctx, rounds=2):
        raise ModelRetry(_DETAILS_RETRY)


def _resolve_season(advice_type: str, season: Optional[str]) -> str:
    """Season to search with: the configured (env) season wins; else the farmer's (personalised only)."""
    farmer_season = _clean_param(season) if advice_type == "personalised" else ""
    if advice_type == "personalised" and not default_season() and farmer_season.lower() not in _SEASONS:
        # No configured season: the farmer must give one, so keep asking until they do.
        raise ModelRetry(
            "The season is required for personalised advice. Ask the farmer which season it is "
            "(Rabi, Kharif or Zaid) and wait for the answer. If they have not given a valid season, "
            "ask again; do not search until they do."
        )
    if farmer_season.lower() in _SEASONS:
        farmer_season = farmer_season.capitalize()
    return default_season() or farmer_season


async def _post_search(payload: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], str]:
    """POST the search to the BAP; returns (response data, error message for the agent)."""
    bap_endpoint = os.getenv("BAP_ENDPOINT")
    if not bap_endpoint:
        logger.error("BAP_ENDPOINT is not set")
        return None, "Advisory search service is not configured. Please try again later."
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                bap_endpoint.rstrip("/") + "/search", json=payload, timeout=DEFAULT_HTTP_TIMEOUT
            )
    except httpx.HTTPError as exc:
        logger.error("Advisory search request failed: %s", exc)
        return None, "Advisory search service is unavailable. Please try again later."
    if response.status_code != 200:
        logger.error("Advisory search returned status %s", response.status_code)
        return None, "Advisory search service is unavailable. Please try again later."
    try:
        return response.json(), ""
    except ValueError:
        logger.error("Advisory search returned a non-JSON body")
        return None, "Advisory search returned an unexpected response. Please try again later."


def _log_results(transaction_id: str, results: List[Dict[str, Any]], search_ctx: Dict[str, str], status: str) -> None:
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


def _no_results_message(query: str, status: str, message: str) -> str:
    if status and status not in ("success", "not_found") and message:
        return message
    return f"No advisory found for `{query}`."


def _top_results(results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    return results[:top_k] if top_k and top_k > 0 else results


@observe(name="tool:search_advisory", as_type="tool")
async def search_advisory(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: str,
    advice_type: Literal["generic", "personalised"],
    days_after_sowing: Optional[int] = None,
    season: Optional[str] = None,
    location: Optional[str] = None,
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
        advice_type: The farmer's own answer to "generic or personalised advice?" (required).
            If the farmer has not answered that question yet, do NOT call this tool: ask them first.
        days_after_sowing: Personalised advice only: whole number of days since the farmer
            sowed the crop. Leave empty for generic advice or when the farmer does not know it.
        season: Personalised advice only: the season the farmer gave (Rabi, Kharif or Zaid). Leave empty
            when the farmer does not know it; the server default season is then used.
        location: A district, state or place the farmer mentioned, if any. It is never used to search
            (advice is filtered on crop, season and days only); it only lets the answer say so.
        top_k: Maximum number of advisories to return (default: 5)

    Returns:
        Advisory text, then a "Search notes" line, or a message saying nothing was found.
    """
    if not farmer_was_asked(ctx):
        raise ModelRetry(ASK_FIRST_RETRY + " Then call search_advisory with advice_type set to their choice.")
    crop = _clean_param(crop)
    if not crop:
        raise ModelRetry("Ask the farmer which crop they mean, then call search_advisory again with `crop`.")
    personalised = advice_type == "personalised"
    # Generic advice never sends the personal details, even if the model passes them.
    days = _clean_days(days_after_sowing) if personalised else None
    _require_details_asked(ctx, personalised, days)
    season_value = _resolve_season(advice_type, season)

    payload = build_advisory_search_payload(
        query=query,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
        crop=crop,
        days_after_sowing=days,
        season=season_value,
    )
    transaction_id = payload["context"]["transaction_id"]
    lf_update_current_observation(
        input={"query": query, "crop": crop, "advice_type": advice_type, "days_after_sowing": days, "season": season_value or None, "top_k": top_k},
        metadata={"tool": "advisory.search", "category": KNOWLEDGE_ADVISORY_CATEGORY, "transaction_id": transaction_id},
    )

    data, error = await _post_search(payload)
    if data is None:
        return error

    results, search_ctx = parse_advisory_response(data)
    status = str(search_ctx.get("status") or "").strip().lower()
    message = str(search_ctx.get("message") or "").strip()
    _log_results(transaction_id, results, search_ctx, status)

    if not results:
        return _no_results_message(query, status, message)
    formatted = format_advisory_results(_top_results(results, top_k), query)
    return formatted + "\n\n" + format_search_notes(
        search_ctx, days, personalised=personalised, location=_clean_param(location)
    )
