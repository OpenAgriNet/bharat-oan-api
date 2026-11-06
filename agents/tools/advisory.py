"""
Crop advisory search through the Beckn network (BAP /search, category knowledge-advisory).

The provider searches advisory chunks that docs-pipeline stores in Qdrant and answers with
an on_search catalog whose items carry `chunk-details` tags: title, crop, section, score,
doc_id, chunk_id, source, text (text already ends with a "Source:" line).

Flow: search first (no questions), then inspect the returned chunks. Every chunk text starts
with a header line such as `Crop: Wheat | Problem type: Pest | Season: Rabi,Kharif`; the season is
read from it. When the farmer named a season that none of the chunks cover, the tool searches once
more (crop + season + days) and, if that finds nothing either, offers the seasons that do exist.
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
from helpers.advisory_pending import (
    LOCATION_MARKER,
    PENDING_MARKER,
    asked_location_in_previous_turn,
    farmer_gave_days,
    farmer_gave_place,
    farmer_gave_season,
    farmer_wrote,
    may_answer_now,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.locations import Place, resolve_place
from helpers.utils import get_logger

logger = get_logger(__name__)

KNOWLEDGE_ADVISORY_CATEGORY = "knowledge-advisory"

# Source shown to the farmer for every advisory answer.
ADVISORY_SOURCE = "ICAR - Kisan Sarathi"

# Longest value forwarded for crop / season, and the accepted range for days after sowing.
_MAX_PARAM_LENGTH = 100
_MAX_DAYS_AFTER_SOWING = 730
# The provider clamps top_k to 1..20; the re-query asks for the most it will return.
_REQUERY_TOP_K = 20
_MAX_TOP_K = 20
_SEASON_ALIASES = {"summer": "Zaid", "zayad": "Zaid"}


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


def _clean_top_k(value: Optional[int]) -> Optional[int]:
    """A whole number in 1..20, else None (the provider default is then used)."""
    try:
        top_k = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return min(top_k, _MAX_TOP_K) if top_k > 0 else None


# ---------------------------------------------------------------------------
# Season handling (read from the chunk text header)
# ---------------------------------------------------------------------------

_SEASON_HEADER = re.compile(r"(?:^|\|)[ \t]*Season:[ \t]*([^|\n]*)", re.MULTILINE)
# Seasons that tie an advisory to no particular season: they match any season the farmer names.
_AGNOSTIC_SEASONS = {"", "other"}


def parse_chunk_seasons(text: str) -> List[str]:
    """Seasons of a chunk, lowercase, from its `Season: Rabi,Kharif` header ([] when absent)."""
    match = _SEASON_HEADER.search(text or "")
    if not match:
        return []
    return [part.strip().lower() for part in re.split(r"[,;/]", match.group(1)) if part.strip()]


def normalize_season(value: Optional[str]) -> str:
    """Farmer's season as displayed (`Rabi`, `Kharif`, `Zaid`); free text is kept, capitalised."""
    cleaned = _clean_param(value)
    if not cleaned:
        return ""
    return _SEASON_ALIASES.get(cleaned.lower(), cleaned.capitalize())


def season_matches(result: Dict[str, Any], season: str) -> bool:
    """True when the advisory applies to `season`. No season, `Other` or an unreadable header = any season."""
    seasons = result.get("seasons") or []
    if not seasons or any(s in _AGNOSTIC_SEASONS for s in seasons):
        return True
    return season.strip().lower() in seasons


def explicit_seasons(results: List[Dict[str, Any]]) -> List[str]:
    """Distinct named seasons (not `Other`) across the results, in first-seen order, capitalised."""
    seen: List[str] = []
    for result in results:
        for season in result.get("seasons") or []:
            if season not in _AGNOSTIC_SEASONS and season not in seen:
                seen.append(season)
    return [s.capitalize() for s in seen]


def _crop_eq(chunk_crop: str, crop: str) -> bool:
    a, b = chunk_crop.strip().lower(), crop.strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def relevant_for_crop(results: List[Dict[str, Any]], crop_hint: str, query: str) -> tuple[List[Dict[str, Any]], str]:
    """Keep the chunks that are about the farmer's crop. Returns (chunks, crop name for a re-query filter).

    The crop is the model's `crop_hint`, else a chunk crop named in the query. When neither is known
    every chunk is kept. An empty list means the search returned chunks of other crops only.
    """
    crops = list(dict.fromkeys(r["crop"] for r in results if r.get("crop")))
    hint = crop_hint.strip()
    if not hint:
        named = [c for c in crops if c.lower() in query.lower()]
        hint = named[0] if named else ""
    if not hint:
        return list(results), ""
    positives = [r for r in results if _crop_eq(r.get("crop") or "", hint)]
    return positives, hint


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

def _search_params_tag(
    crop: str,
    days_after_sowing: Optional[int],
    season: str = "",
    top_k: Optional[int] = None,
    state_lgd_code: Optional[int] = None,
    district_lgd_code: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """`search_params` tag group the provider reads: crop, days_after_sowing, season, top_k and the farmer's
    place as LGD codes (only when the farmer gave one)."""
    entries = []
    for code, value in (
        ("crop", crop),
        ("days_after_sowing", days_after_sowing),
        ("season", season),
        ("top_k", top_k),
        ("state_lgd_code", state_lgd_code),
        ("district_lgd_code", district_lgd_code),
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
    top_k: Optional[int] = None,
    state_lgd_code: Optional[int] = None,
    district_lgd_code: Optional[int] = None,
) -> Dict[str, Any]:
    """Beckn /search body for advisory document search (knowledge-advisory).

    `crop`, `days_after_sowing`, `season`, `top_k` and the farmer's place as `state_lgd_code` /
    `district_lgd_code` travel in `item.tags` (group `search_params`). The provider filters on `crop` and
    only boosts `season`, days and the place.
    """
    now = datetime.now(timezone.utc)
    item: Dict[str, Any] = {"descriptor": {"name": query}}
    tags = _search_params_tag(
        _clean_param(crop),
        _clean_days(days_after_sowing),
        _clean_param(season),
        _clean_top_k(top_k),
        state_lgd_code,
        district_lgd_code,
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


def _split_names(value: Optional[str]) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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
        "seasons": parse_chunk_seasons(text),
        # Where the advisory applies. Chunks from a provider without location read as "applies everywhere".
        "location_match": (details.get("location_match") or "not_requested").strip().lower(),
        "location_national": (details.get("location_national") or "true").strip().lower() != "false",
        "location_states": _split_names(details.get("location_states")),
        "location_districts": _split_names(details.get("location_districts")),
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


def _places_of(result: Dict[str, Any]) -> str:
    """Where an advisory is meant for, as text (districts with their states, or the states)."""
    districts, states = result.get("location_districts") or [], result.get("location_states") or []
    if districts and states:
        return f"{', '.join(districts)} ({', '.join(states)})"
    return ", ".join(districts or states)


def _join_places(results: List[Dict[str, Any]]) -> str:
    """Distinct places (as `_places_of`) of the given advisories, joined for a sentence."""
    seen: List[str] = []
    for result in results:
        text = _places_of(result)
        if text and text not in seen:
            seen.append(text)
    return _join_names(seen) if seen else ""


def location_opening(
    results: List[Dict[str, Any]],
    place: Optional[Place] = None,
    named_place: str = "",
) -> str:
    """The first sentence of the answer about how the advice relates to the farmer's place ('' when no place matters)."""
    place = place or Place()
    label = place.label or named_place
    where = place.district_name or place.state_name or "place"
    specific = [r for r in results if not r.get("location_national", True)]
    matched = [r for r in specific if r.get("location_match") == "matched"]
    partial = [r for r in specific if r.get("location_match") == "state_partial"]
    other = [r for r in specific if r.get("location_match") not in ("matched", "state_partial")]
    has_general = len(specific) < len(results)

    if not specific:
        return f"There is no advisory specific to {label}. This is the general advice found for your crop." if label else ""
    if matched:
        return f"This advice is for {_join_places(matched)}, which covers your {where}."
    if partial:
        return f"This advice is for {_join_places(partial)}, which are some districts of your state."
    if not label:
        return f"This advice is meant for {_join_places(other)}, so it may not apply to you."
    if has_general:
        return (
            f"There is no advisory specific to {label}. Below is general advice for your crop, and an advisory meant "
            f"for {_join_places(other)}, which may not apply to you."
        )
    return f"No advisory is available for {label}. This advice is meant for {_join_places(other)}, so it may not apply to you."


def format_location_notes(
    results: List[Dict[str, Any]],
    place: Optional[Place] = None,
    named_place: str = "",
) -> List[str]:
    """Lines telling the agent how each advisory relates to the farmer's place, and what to say about it."""
    place = place or Place()
    opening = location_opening(results, place, named_place)
    if not opening:
        return []
    lines = [
        "> Open your answer with this one sentence as the very first line (translate it to the farmer's language, "
        f"keep the places exactly as written): \"{opening}\" Then give the advice. Give only what the advisory text "
        "above says; do not add steps or details from your own knowledge."
    ]
    if not any(not r.get("location_national", True) for r in results):
        return lines
    for result in results:
        title = result.get("title") or "Advisory"
        match = result.get("location_match") or "not_requested"
        if result.get("location_national", True):
            lines.append(f"> '{title}' applies everywhere (not tied to a place).")
        elif match == "matched":
            lines.append(f"> '{title}' is for {_places_of(result)}, which covers the farmer's place.")
        elif match == "state_partial":
            lines.append(f"> '{title}' is for {_places_of(result)}: only some districts of the farmer's state.")
        else:
            lines.append(f"> '{title}' is meant for {_places_of(result)}.")
    return lines


def format_search_notes(
    search_ctx: Dict[str, str],
    days_after_sowing: Optional[int],
    location: str = "",
    place: Optional[Place] = None,
    results: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Notes for the agent about how the search was applied, with what to tell the farmer."""
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    days_matched = str(search_ctx.get("days_matched") or "").strip().lower() == "true"
    facts = [f"advisory_has_day_range={'yes' if day_range else 'no'}"]
    if days_after_sowing is not None:
        facts.append(f"days_after_sowing_matched={'yes' if days_matched else 'no'}")
    lines = ["> Search notes: " + "; ".join(facts)]
    place = place or Place()
    if location and not place.known:
        lines.append(
            f"> The farmer named a place ({location}) that could not be matched to a state or district, so the "
            "advisories were not narrowed by place. Open your answer with this one sentence as the very first line "
            f"(translate it to the farmer's language): \"I could not match {location} to a state or district. This is "
            "the advice found for your crop.\" Then give the advice."
        )
    lines.extend(format_location_notes(results or [], place, location if not place.known else ""))
    if days_after_sowing is not None and (not day_range or not days_matched):
        lines.append(
            f"> The farmer did give the days since sowing ({days_after_sowing}), but this advisory has no guidance "
            "for that sowing stage. Do not say the farmer did not give the days. Say briefly that this is the "
            "general advice for the crop and season."
        )
    elif days_after_sowing is None and not day_range:
        lines.append("> This advice is general for the crop and not tied to a sowing stage. Say so briefly.")
    return "\n".join(lines)


def _join_names(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def format_details_question(seasons: List[str], need_season: bool, need_days: bool, query: str) -> str:
    """Result when the farmer's season and/or days are missing: the agent asks ONE short question."""
    args, parts = [], []
    if need_season:
        args.append("`season`")
        parts.append("which season your crop is in")
    if need_days:
        args.append("`days_after_sowing`")
        parts.append("how many days ago you sowed it")
    example = "Could you please tell me which season you are growing your crop in, and how many days ago you sowed it?" if need_season and need_days else (
        "Could you please tell me which season you are growing your crop in?" if need_season else "Could you please tell me how many days ago you sowed your crop?"
    )
    # Name the seasons only when there is a real choice. With a single season, saying so makes the question
    # read as if that season were expected, and the farmer may well be in another one.
    seasons_note = (
        f" The advisories found cover the {_join_names(seasons)} seasons; you may name them in a plain sentence "
        "as the choices."
        if need_season and len(seasons) > 1
        else ""
    )
    return (
        f"{PENDING_MARKER}\n> Advisory Search Results for `{query}`\n\n"
        "> Advisories were found, but the right one depends on details the farmer has not given. Do not give any "
        "advice yet. Your whole reply is only that one question: no introduction or explanation of why you are "
        "asking, no repeated sentence, and no Source line. Ask the farmer ONE short, natural question asking "
        + " and ".join(parts)
        + f". Example (use the farmer's crop name instead of \"your crop\" and translate it to the farmer's language): \"{example}\"." + seasons_note
        + " Do not suggest which season it is or say which season the advice is for: the farmer decides."
        " Write it as normal sentences: never put season names in brackets or in a list."
        " When they answer, call `search_advisory` again with the same `query` and `crop` plus their "
        + " and ".join(args)
        + ". If they say they do not know or do not want to answer, call it again with `answer_now=true`."
    )


def format_season_suggestion(season: str, subject: str, seasons: List[str]) -> str:
    """Result when no advisory exists for the farmer's season: offer the seasons that do."""
    return (
        f"{PENDING_MARKER}\n> No advisory is available for the {season} season for {subject}. Advisories are available for the "
        f"{' / '.join(seasons)} season. Tell the farmer in one short sentence that nothing is available for "
        f"{season}, and ask whether they would like the advice for {' or '.join(seasons)}. Do not give any advice "
        "yet and do not show a Source line. If they say yes, call `search_advisory` again with the same `query` "
        "and that `season`."
    )


def format_location_question(query: str, restate: str = "") -> str:
    """Result when the advisories depend on the farmer's place and they have not given it: ask for it."""
    return (
        f"{PENDING_MARKER}{LOCATION_MARKER}\n> Advisory Search Results for `{query}`\n\n"
        "> These advisories are meant for particular places, and the farmer has not said where they farm. Do not give "
        "any advice yet. Your whole reply is only that one question: no introduction or explanation of why you are "
        "asking, no repeated sentence, and no Source line. Ask the farmer ONE short, natural question asking their "
        "state and district. Example (translate it to the farmer's language): \"Could you please tell me which state "
        "and district you are farming in?\" Write it as a normal sentence. When they answer, call `search_advisory` "
        f"again with the same `query` and `crop`{restate} plus `state` and `district` (the names they gave, in English). "
        "If they say they do not know or do not want to say, call it again with `answer_now=true`."
    )


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


def _log_results(
    transaction_id: str,
    results: List[Dict[str, Any]],
    search_ctx: Dict[str, str],
    status: str,
    **extra: Any,
) -> None:
    lf_update_current_observation(
        metadata={
            "tool": "advisory.search",
            "category": KNOWLEDGE_ADVISORY_CATEGORY,
            "transaction_id": transaction_id,
            "network_status": status or None,
            "location_status": search_ctx.get("location_status") or None,
            "has_day_range": search_ctx.get("has_day_range") or None,
            "days_matched": search_ctx.get("days_matched") or None,
            "has_location_specific": search_ctx.get("has_location_specific") or None,
            "hit_count": len(results),
            "doc_ids": list(dict.fromkeys(r["doc_id"] for r in results if r["doc_id"])),
            "chunk_ids": [r["chunk_id"] for r in results if r["chunk_id"]],
            "seasons_in_chunks": explicit_seasons(results),
            **extra,
        }
    )


def _no_results_message(query: str, status: str, message: str) -> str:
    if status and status not in ("success", "not_found") and message:
        return message
    return f"No advisory found for `{query}`."


def _top_results(results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    return results[:top_k] if top_k and top_k > 0 else results


async def _run_search(
    ctx: RunContext[FarmerContext],
    query: str,
    days: Optional[int],
    season: str,
    crop: str = "",
    top_k: Optional[int] = None,
    requery: bool = False,
    place: Optional[Place] = None,
) -> tuple[Optional[List[Dict[str, Any]]], Dict[str, str], str, str]:
    """One BAP search. Returns (results or None on a transport error, search context, status, message/error)."""
    payload = build_advisory_search_payload(
        query=query,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
        crop=crop,
        days_after_sowing=days,
        season=season,
        top_k=top_k,
        state_lgd_code=place.state_code if place else None,
        district_lgd_code=place.district_code if place else None,
    )
    transaction_id = payload["context"]["transaction_id"]
    lf_update_current_observation(
        input={"query": query, "crop": crop or None, "days_after_sowing": days, "season": season or None, "top_k": top_k,
               "state_lgd_code": place.state_code if place else None,
               "district_lgd_code": place.district_code if place else None},
        metadata={"tool": "advisory.search", "category": KNOWLEDGE_ADVISORY_CATEGORY, "transaction_id": transaction_id},
    )
    data, error = await _post_search(payload)
    if data is None:
        return None, {}, "", error
    results, search_ctx = parse_advisory_response(data)
    status = str(search_ctx.get("status") or "").strip().lower()
    message = str(search_ctx.get("message") or "").strip()
    _log_results(transaction_id, results, search_ctx, status, requery=requery)
    return results, search_ctx, status, message


@observe(name="tool:search_advisory", as_type="tool")
async def search_advisory(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: str,
    days_after_sowing: Optional[int] = None,
    season: Optional[str] = None,
    location: Optional[str] = None,
    top_k: int = 5,
    answer_now: bool = False,
    state: Optional[str] = None,
    district: Optional[str] = None,
) -> str:
    """
    Search crop advisories for a farming problem: pests, diseases, symptoms, crop practices
    and harvesting guidance for a specific crop, from ICAR - Kisan Sarathi.

    Use this for crop-advisory questions such as "aphids in wheat" or "when to harvest
    sorghum". Search straight away; do not ask the farmer anything first. Do NOT use it for
    government scheme questions (use search_schemes) or for weather, mandi prices or status checks.

    The tool looks at the advisories it finds. If they depend on the season, the sowing stage or the
    farmer's place and the farmer has not said it, the tool tells you to ask ONE short question; ask it,
    then call again.

    Args:
        query: The farming problem in *English*, 2-5 words, including the crop (required)
        crop: The crop in English (for example "Wheat") (required). If the farmer has not said which crop,
            do not call this tool: ask which crop first.
        days_after_sowing: Whole number of days since the farmer sowed the crop, if the farmer said it.
        season: Rabi, Kharif or Zaid, if the farmer said it (also in the first message).
        location: A place the farmer mentioned that is not clearly a state or district (a village, a town),
            if any. It is only mentioned in the answer.
        top_k: Maximum number of advisories to return (default: 5)
        answer_now: Set true only when the farmer said they do not know the season, the days or their place
            (or do not want to say): the tool then answers without asking again.
        state: The farmer's state in English (for example "Uttar Pradesh"), only if the farmer said it.
        district: The farmer's district in English (for example "Agra"), only if the farmer said it.

    Returns:
        Advisory text with a "Search notes" line; or an instruction to ask the farmer one question; or an
        offer of the seasons that are available; or a message saying nothing was found.
    """
    days = _clean_days(days_after_sowing)
    season_value = normalize_season(season)
    # Hard guards: details only the farmer can give are ignored when the conversation does not show them.
    if season_value and not farmer_gave_season(ctx):
        logger.info("search_advisory: ignoring season %r the farmer did not give", season_value)
        season_value = None
    if days is not None and not farmer_gave_days(ctx):
        logger.info("search_advisory: ignoring days_after_sowing %r the farmer did not give", days)
        days = None
    if answer_now and not may_answer_now(ctx):
        logger.info("search_advisory: ignoring answer_now before the farmer was asked")
        answer_now = False
    # The farmer's place: only what they said (or answered when asked) counts; the model may not fill it in.
    place = resolve_place(_clean_param(state), _clean_param(district), _clean_param(location))
    if place.known and not farmer_gave_place(ctx):
        logger.info("search_advisory: ignoring place %r the farmer did not give", place.label)
        place = Place()
    location_value = _clean_param(location)
    if location_value and not farmer_wrote(ctx, location_value):
        logger.info("search_advisory: ignoring location %r the farmer did not give", location_value)
        location_value = ""
    # A place the farmer named that we could not match to a state or district (any of the three fields).
    typed_place = ", ".join(v for v in (_clean_param(district), _clean_param(state), location_value) if v)
    named_place = "" if place.known else typed_place
    if named_place and not farmer_wrote(ctx, named_place.split(", ")[0]):
        named_place = ""
    crop_hint = _clean_param(crop)
    if not crop_hint:
        raise ModelRetry(
            "The crop is required. If the farmer named a crop, call search_advisory again with `crop`. "
            "If they did not, ask them which crop in one short question, wait for the answer, then search."
        )
    limit = _clean_top_k(top_k) or 5

    results, search_ctx, status, message = await _run_search(
        ctx, query, days, season_value, top_k=limit, place=place
    )
    if results is None:
        return message  # transport error text for the agent
    if not results:
        return _no_results_message(query, status, message)

    chunks, crop_name = relevant_for_crop(results, crop_hint, query)
    seen = list(results)  # every chunk looked at, for the seasons offered later
    season_ok = [r for r in chunks if season_matches(r, season_value)] if season_value else chunks

    # One re-query with every filter when the chunks are about another crop, or miss the farmer's season.
    if not chunks or (season_value and not season_ok):
        results2, search_ctx2, _, _ = await _run_search(
            ctx, query, days, season_value, crop=crop_name, top_k=_REQUERY_TOP_K, requery=True, place=place
        )
        chunks2 = relevant_for_crop(results2 or [], crop_hint, query)[0] if crop_name else list(results2 or [])
        seen += results2 or []
        if results2 is not None and chunks2:
            chunks, search_ctx = chunks2, search_ctx2
            season_ok = [r for r in chunks if season_matches(r, season_value)] if season_value else chunks
        elif not chunks:
            return f"No advisory found for `{query}`."

    if season_value and not season_ok:
        seasons = explicit_seasons([r for r in seen if not crop_name or _crop_eq(r.get("crop") or "", crop_name)])
        lf_update_current_observation(
            metadata={"season_requested": season_value, "season_match": False, "suggested_seasons": seasons}
        )
        return format_season_suggestion(season_value, crop_name or f"`{query}`", seasons)

    # Ask the farmer for the season (when they gave none) and the days whenever the advisories carry that detail.
    seasons = [] if season_value else explicit_seasons(season_ok)
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    need_season, need_days = bool(seasons), day_range and days is None
    if (need_season or need_days) and not answer_now:
        return format_details_question(seasons, need_season, need_days, query)

    # Then the farmer's place, only when the advisories we would answer from are tied to places.
    top = _top_results(season_ok, limit)
    place_matters = any(not r.get("location_national", True) for r in top)
    if place_matters and not place.known and not named_place and not answer_now and not asked_location_in_previous_turn(ctx):
        restate = (f", `season`=\"{season_value}\"" if season_value else "") + (
            f", `days_after_sowing`={days}" if days is not None else ""
        )
        return format_location_question(query, restate)
    return _answer(season_ok, search_ctx, query, limit, days, named_place, place)


def _answer(
    results: List[Dict[str, Any]],
    search_ctx: Dict[str, str],
    query: str,
    limit: int,
    days: Optional[int],
    location: str,
    place: Optional[Place] = None,
) -> str:
    top = _top_results(results, limit)
    return (
        format_advisory_results(top, query)
        + "\n\n"
        + format_search_notes(search_ctx, days, location=location, place=place, results=top)
    )
