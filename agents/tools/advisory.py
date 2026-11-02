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
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
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


# Appears in the tool text when the farmer's district has no advisory data, so the guard below can
# tell that the farmer was (or was not) asked whether to continue without the district.
NO_DISTRICT_DATA_MARKER = "[NO_ADVISORY_DATA_FOR_DISTRICT]"


def _asked_about_district_fallback(ctx: RunContext[FarmerContext]) -> bool:
    """True when the "no data for your district" result was followed by an assistant message.

    That message is the question to the farmer, so a later call with district_fallback_confirmed
    is the answer to it. A confirmation in the same step as the "no data" result is refused.
    """
    messages = getattr(ctx, "messages", None)
    if messages is None:  # cannot verify (not running inside an agent run)
        return True
    seen_marker = False
    for message in messages:
        if isinstance(message, ModelRequest):
            if any(
                isinstance(part, ToolReturnPart)
                and part.tool_name == "search_advisory"
                and NO_DISTRICT_DATA_MARKER in str(part.content)
                for part in message.parts
            ):
                seen_marker = True
        elif seen_marker and isinstance(message, ModelResponse):
            if any(isinstance(part, TextPart) and part.content.strip() for part in message.parts):
                return True
    return False


def _farmer_was_asked(ctx: RunContext[FarmerContext]) -> bool:
    """True when, for the current advisory question, the farmer was asked and has answered.

    Looks at the messages after the last completed advisory search (or the whole conversation when
    there was none) for this order: the farmer's question, then an assistant message (the
    "generic or personalised?" question), then the farmer's reply. So every new advisory question
    needs its own ask, and an earlier choice is not reused.
    """
    messages = getattr(ctx, "messages", None)
    if messages is None:  # cannot verify (not running inside an agent run)
        return True
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, ModelRequest) and any(
            isinstance(part, ToolReturnPart)
            and part.tool_name == "search_advisory"
            and NO_DISTRICT_DATA_MARKER not in str(part.content)
            for part in message.parts
        ):
            start = index + 1
    stage = 0  # 0: wait for the question, 1: wait for the ask, 2: wait for the reply
    for message in messages[start:]:
        if isinstance(message, ModelRequest):
            if any(isinstance(part, UserPromptPart) for part in message.parts):
                if stage == 0:
                    stage = 1
                elif stage == 2:
                    return True
        elif isinstance(message, ModelResponse) and stage == 1:
            if any(isinstance(part, TextPart) and part.content.strip() for part in message.parts):
                stage = 2
    return False


def default_season() -> str:
    """Season configured in env `ADVISORY_DEFAULT_SEASON` (empty when not set)."""
    return _clean_param(os.getenv("ADVISORY_DEFAULT_SEASON"))


def advisory_season_note() -> str:
    """System-prompt note telling the agent how the season is handled (depends on env)."""
    season = default_season()
    ask = (
        "In the personalised crop advisory flow, ask the farmer for the **season** (Rabi, Kharif or Zaid) "
        "together with the district and the days since sowing. The season is mandatory: pass their answer "
        "as `season` and ask again until they give one. Do not ask for the season in the generic flow."
    )
    if season:
        return (
            f"\n\n## Advisory season (fixed: {season})\n{ask} Whatever the farmer answers, the advice is "
            f"always searched for the **{season}** season: do not pass `season` and do not use their answer. "
            f"When your answer uses the crop advisory results, say it is for the {season} season and continue "
            f"the sentence with the advice, for example \"For the {season} season, ...\". Do this in the "
            f"generic flow too."
        )
    return (
        "\n\n## Advisory season (asked from the farmer)\n" + ask + " The season is mandatory: pass their answer as `season`, and "
        "if they do not give one of Rabi, Kharif or Zaid, ask again until they do."
    )


def _search_params_tag(
    crop: str, district: str, days_after_sowing: Optional[int], season: str = ""
) -> List[Dict[str, Any]]:
    """`search_params` tag group the provider reads: crop, district, days_after_sowing, season."""
    entries = []
    for code, value in (
        ("crop", crop),
        ("district", district),
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
    district: str = "",
    days_after_sowing: Optional[int] = None,
    season: str = "",
) -> Dict[str, Any]:
    """Beckn /search body for advisory document search (knowledge-advisory).

    `crop`, `district`, `days_after_sowing` and `season` travel in `item.tags` (group `search_params`).
    """
    now = datetime.now(timezone.utc)
    item: Dict[str, Any] = {"descriptor": {"name": query}}
    tags = _search_params_tag(
        _clean_param(crop), _clean_param(district), _clean_days(days_after_sowing), _clean_param(season)
    )
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
    district_given: bool = False,
    district_unavailable: bool = False,
) -> str:
    """Notes for the agent about how the search was applied, with what to tell the farmer."""
    location = str(search_ctx.get("location_status") or "").strip().lower()
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    days_matched = str(search_ctx.get("days_matched") or "").strip().lower() == "true"
    facts = []
    if location:
        facts.append(f"location_status={location}")
    facts.append(f"advisory_has_day_range={'yes' if day_range else 'no'}")
    if days_after_sowing is not None:
        facts.append(f"days_after_sowing_matched={'yes' if days_matched else 'no'}")
    lines = ["> Search notes: " + "; ".join(facts)]
    if district_unavailable:
        lines.append(
            "> The farmer's district has no advisory data and the farmer agreed to the crop advice. "
            "Say in one short sentence that this advice is for the crop and not specific to their district."
        )
    elif location == "location_not_applied":
        lines.append(
            "> These advisories are not specific to the farmer's district. Say so in one short sentence "
            "and do not present the advice as local to their district."
        )
    if not day_range or (days_after_sowing is not None and not days_matched):
        lines.append(
            "> This advice is general for the crop and not matched to the days since sowing. Say so briefly."
        )
    if personalised and not district_given:
        lines.append(
            "> The farmer did not give a district, so the location was skipped. Say in one short sentence "
            "that the advice is not specific to their area."
        )
    if personalised and days_after_sowing is None:
        lines.append(
            "> The farmer did not give the days since sowing. Say in one short sentence that the advice "
            "is not matched to their sowing stage."
        )
    return "\n".join(lines)


@observe(name="tool:search_advisory", as_type="tool")
async def search_advisory(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: str,
    advice_type: Literal["generic", "personalised"],
    district: Optional[str] = None,
    days_after_sowing: Optional[int] = None,
    district_fallback_confirmed: bool = False,
    season: Optional[str] = None,
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
        district: Personalised advice only: the farmer's district (or state if the district
            is unknown). Leave empty for generic advice or when the farmer does not know it.
        days_after_sowing: Personalised advice only: whole number of days since the farmer
            sowed the crop. Leave empty for generic advice or when the farmer does not know it.
        district_fallback_confirmed: Set to true ONLY after the tool said there is no advisory data
            for the district, you asked the farmer whether they want the crop advice anyway, and
            they agreed. Leave false otherwise.
        season: Personalised advice only: the season the farmer gave (Rabi, Kharif or Zaid). Leave empty
            when the farmer does not know it; the server default season is then used.
        top_k: Maximum number of advisories to return (default: 5)

    Returns:
        Advisory text, then a "Search notes" line, or a message saying nothing was found.
    """
    if not _farmer_was_asked(ctx):
        raise ModelRetry(
            "Do not search yet. Ask the farmer whether they want generic or personalised advice, "
            "wait for their answer, then call search_advisory with advice_type set to their choice."
        )
    crop = _clean_param(crop)
    if not crop:
        raise ModelRetry("Ask the farmer which crop they mean, then call search_advisory again with `crop`.")
    # Generic advice never sends the personal details, even if the model passes them.
    district = _clean_param(district) if advice_type == "personalised" else ""
    days = _clean_days(days_after_sowing) if advice_type == "personalised" else None

    # The configured season (env) always wins; the farmer's answer (personalised only) is used when none is set.
    farmer_season = _clean_param(season) if advice_type == "personalised" else ""
    if advice_type == "personalised":
        # The farmer must give a season: keep asking until they do (the env season is applied after).
        if farmer_season.lower() not in _SEASONS:
            raise ModelRetry(
                "The season is required for personalised advice. Ask the farmer which season it is "
                "(Rabi, Kharif or Zaid) and wait for the answer. If they have not given a valid season, "
                "ask again; do not search until they do."
            )
        farmer_season = farmer_season.capitalize()
    season_value = default_season() or farmer_season

    # The farmer agreed to the crop advice without the district: search without it.
    district_unavailable = False
    if district_fallback_confirmed:
        if not _asked_about_district_fallback(ctx):
            raise ModelRetry(
                "Do not set district_fallback_confirmed yet. First tell the farmer there is no advisory data "
                "for their district and ask whether they want the crop advice anyway; use their answer."
            )
        district = ""
        district_unavailable = True

    payload = build_advisory_search_payload(
        query=query,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
        crop=crop,
        district=district,
        days_after_sowing=days,
        season=season_value,
    )
    transaction_id = payload["context"]["transaction_id"]
    lf_update_current_observation(
        input={"query": query, "crop": crop, "advice_type": advice_type, "district": district or None, "days_after_sowing": days, "season": season_value or None, "top_k": top_k},
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
    if district and str(search_ctx.get("location_status") or "").strip().lower() == "location_not_applied":
        return (
            f"No advisory data is available for the district '{district}'. {NO_DISTRICT_DATA_MARKER}\n"
            "Do not give any advice yet. Tell the farmer, in their language, that there is no advisory data "
            f"for their district, and ask whether they want advice for {crop} anyway"
            + (" (matched to their sowing days)" if days is not None else "")
            + ". If they agree, call search_advisory again with the same arguments plus "
            "district_fallback_confirmed=true. If they decline, do not search again."
        )
    formatted = format_advisory_results(results[:top_k] if top_k and top_k > 0 else results, query)
    return formatted + "\n\n" + format_search_notes(
        search_ctx,
        days,
        personalised=advice_type == "personalised",
        district_given=bool(district) or district_unavailable,
        district_unavailable=district_unavailable,
    )
