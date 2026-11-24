"""
Crop and livestock advisory search through the Knowledge Provider (POST /search, search_profile advisory).

The one advisory tool: every crop, pest, disease and livestock question goes to KP, and a crop photo goes to
NPSS for pest/disease identification. Regional crop/pest words are mapped to their English glossary term first.

KP filters advisory passages on the crop and boosts season, days after sowing and the farmer's
place. Each hit carries text (already ending with a "Source:" line), doc_id, chunk_id, section,
location_match and the advisory's LGD places; `advisory_search` carries the search status.

Flow: search first (no questions), then inspect the returned chunks. Every chunk text starts
with a header line such as `Crop: Wheat | Problem type: Pest | Season: Rabi,Kharif`; the season is
read from it. When the farmer named a season that none of the chunks cover, the tool searches once
more (crop + season + days) and, if that finds nothing either, answers generally and offers the
seasons that do exist.

Answer first: the farmer always gets advice. When a detail the farmer has not given would make it more
specific (which crop, the season and sowing days, then the place), the advice is given as general advice and
the tool tells the agent to end the answer with ONE follow-up question for that detail, asked only once.
"""
import re
from typing import Any, Dict, List, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.imd_advisory import agromet_note
from agents.tools.npss import analyze_crop_image
from agents.tools.terms import to_english
from helpers import knowledge_provider
from helpers.advisory_pending import (
    CROP_MARKER,
    LOCATION_MARKER,
    PENDING_MARKER,
    SEASON_MARKER,
    asked_crop_in_previous_turn,
    asked_location_in_previous_turn,
    asked_season_in_previous_turn,
    answer_chain,
    farmer_gave_days,
    farmer_gave_season,
    describe_turns,
    farmer_named_crop,
    farmer_wrote,
    place_words,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.locations import Place, resolve_place
from helpers.utils import get_logger

logger = get_logger(__name__)

KNOWLEDGE_ADVISORY_CATEGORY = "knowledge-advisory"

# Source shown to the farmer for advisories from Kisan Sarathi.
ADVISORY_SOURCE = "ICAR - Kisan Sarathi"
# The provider's `origin` for an advisory PDF uploaded in docs-pipeline; those cite the PDF's name.
PDF_ADVISORY_ORIGIN = "docs-pipeline"
# Crop of the coffee PDF advisories. They carry no season, sowing stage or place, so they are answered at once.
COFFEE_CROP = "coffee"

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


_DAY_HEADER = re.compile(r"^[ \t]*(Start|End) day relative to sowing:[ \t]*(-?\d+)", re.MULTILINE | re.IGNORECASE)


def parse_chunk_days(text: str) -> Optional[tuple]:
    """(start, end) days after sowing from the chunk's `Start/End day relative to sowing` lines; None when absent
    or both 0 (no range given). Negative days are before sowing (field preparation)."""
    found = {key.lower(): int(value) for key, value in _DAY_HEADER.findall(text or "")}
    days = (found.get("start"), found.get("end"))
    return days if None not in days and days != (0, 0) else None


def for_days(results: List[Dict[str, Any]], days: Optional[int]) -> List[Dict[str, Any]]:
    """The advisories whose sowing-day range covers the farmer's `days`, plus those with no range.

    When none of them fits, all are kept: the search notes then say the advice has no guidance for that stage."""
    if days is None:
        return results
    fitting = [r for r in results if not r.get("day_range") or r["day_range"][0] <= days <= r["day_range"][1]]
    return fitting or results


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


def is_coffee_pdf(result: Dict[str, Any]) -> bool:
    """True for a coffee chunk from an uploaded PDF (provider `origin` docs-pipeline, crop Coffee)."""
    return result.get("origin") == PDF_ADVISORY_ORIGIN and _crop_eq(result.get("crop") or "", COFFEE_CROP)


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

def build_advisory_search_payload(
    query: str,
    crop: str = "",
    days_after_sowing: Optional[int] = None,
    season: str = "",
    top_k: Optional[int] = None,
    state_lgd_code: Optional[int] = None,
    district_lgd_code: Optional[int] = None,
) -> Dict[str, Any]:
    """KP /search body for the advisory profile.

    The farmer's situation travels in `advisory`: KP filters on `crop` and only boosts `season`,
    `days_after_sowing` and the place (`state_lgd_code` / `district_lgd_code`).
    """
    advisory = {
        key: value
        for key, value in (
            ("crop", _clean_param(crop)),
            ("season", _clean_param(season)),
            ("days_after_sowing", _clean_days(days_after_sowing)),
            ("state_lgd_code", state_lgd_code),
            ("district_lgd_code", district_lgd_code),
        )
        if value is not None and value != ""
    }
    body: Dict[str, Any] = {"query": query, "search_profile": "advisory"}
    top_k = _clean_top_k(top_k)
    if top_k:
        body["top_k"] = top_k
    if advisory:
        body["advisory"] = advisory
    return body


def _advisory_result_from_hit(hit: Dict[str, Any], score: float, crop: str) -> Optional[Dict[str, Any]]:
    """Map one KP hit to an advisory result dict (None when it has no text).

    `crop` is the crop KP filtered on; without one, the hit's first crop key is used.
    """
    if not isinstance(hit, dict):
        return None
    text = str(hit.get("text") or "").strip()
    if not text:
        return None
    place = hit.get("advisory") if isinstance(hit.get("advisory"), dict) else {}
    crop_keys = hit.get("crop_keys") if isinstance(hit.get("crop_keys"), list) else []
    return {
        "title": str(hit.get("title_en") or hit.get("name_en") or hit.get("title") or "").strip(),
        "crop": crop or (str(crop_keys[0]) if crop_keys else ""),
        "section": str(hit.get("section") or "").strip().lower(),
        "score": score,
        "doc_id": str(hit.get("doc_id") or "").strip(),
        "chunk_id": str(hit.get("chunk_id") or hit.get("_id") or "").strip(),
        "source": str(hit.get("origin") or "").strip(),
        "origin": str(hit.get("origin") or "").strip().lower(),
        "text": text,
        "seasons": parse_chunk_seasons(text),
        "day_range": parse_chunk_days(text),
        # Where the advisory applies. Passages indexed without places read as "applies everywhere".
        "location_match": str(hit.get("location_match") or "not_requested").strip().lower(),
        "location_national": place.get("location_national", True) is not False,
        "location_states": [str(n) for n in place.get("location_state_names") or [] if n],
        "location_districts": [str(n) for n in place.get("location_district_names") or [] if n],
    }


def parse_advisory_response(data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Parse the KP /search response into (advisory results, search-context map)."""
    if not isinstance(data, dict):
        return [], {}
    search_ctx = knowledge_provider.context_strings(data.get("advisory_search"))
    crop = search_ctx.get("crop", "")
    results = []
    for hit in data.get("hits") or []:
        parsed = _advisory_result_from_hit(hit, knowledge_provider.hit_score(hit, data) if isinstance(hit, dict) else 0.0, crop)
        if parsed:
            results.append(parsed)
    return results, search_ctx


def _same_words(a: str, b: str) -> bool:
    return " ".join(re.findall(r"\w+", a.lower())) == " ".join(re.findall(r"\w+", b.lower()))


_TRAILING_SOURCE = re.compile(r"\n+Source:[^\n]*\s*$")


def advisory_source(result: Dict[str, Any]) -> str:
    """Source to cite for one advisory: the PDF's name for an uploaded PDF, else ADVISORY_SOURCE."""
    if result.get("origin") == PDF_ADVISORY_ORIGIN and result.get("title"):
        return result["title"]
    return ADVISORY_SOURCE


def format_advisory_results(results: List[Dict[str, Any]], query: str) -> str:
    """Readable block per advisory for the agent.

    The provider appends "Source: institute, creator" to each text. That line names the
    institute and a user name, so it is removed here; the answer cites advisory_source instead.
    """
    blocks = []
    for r in results:
        text = _TRAILING_SOURCE.sub("", r["text"]).rstrip()
        blocks.append(f"**{r.get('title') or 'Advisory'}**\n```\n{text}\n```")
    sources = list(dict.fromkeys(advisory_source(r) for r in results))
    return (
        "> Advisory Search Results for `" + query + "`\n\n"
        + "\n\n----\n\n".join(blocks)
        + f"\n\n> Cite as (in bold): **Source: {'; '.join(sources)}**"
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


def _district_list(results: List[Dict[str, Any]], limit: int = 4) -> str:
    """District names of the given advisories for a sentence: all of them when few, else the first few and a count."""
    names: List[str] = []
    for result in results:
        for name in result.get("location_districts") or []:
            if name not in names:
                names.append(name)
    if len(names) > limit:
        return f"{', '.join(names[:limit])} and {len(names) - limit} more"
    return _join_names(names)


def location_opening(
    results: List[Dict[str, Any]],
    place: Optional[Place] = None,
    named_place: str = "",
    unmatched: bool = False,
) -> str:
    """The first sentence of the answer about how the advice relates to the farmer's place ('' when no place matters).

    Advice that is not for the farmer's place is offered as general advice: the places it was written for are not
    named."""
    place = place or Place()
    label = place.label or named_place
    where = place.district_name or place.state_name or "place"
    specific = [r for r in results if not r.get("location_national", True)]
    matched = [r for r in specific if r.get("location_match") == "matched"]
    partial = [r for r in specific if r.get("location_match") == "state_partial"]

    if unmatched and named_place and not place.known:
        return f"I could not match {named_place} to a state or district. This is the general advice found for your crop."
    if matched:
        listed = [r for r in matched if r.get("location_districts")]
        if listed and place.district_name:
            state = place.state_name or (listed[0].get("location_states") or [""])[0]
            return f"This advice is for your district, {place.district_name}" + (f", in {state}." if state else ".")
        state = place.state_name or (matched[0].get("location_states") or [""])[0]
        return f"This advice covers all of {state}." if state else "This advice covers your area."
    if partial:
        state = place.state_name or (partial[0].get("location_states") or [""])[0]
        return f"This advice is for some districts of {state}: {_district_list(partial)}."
    if label:
        return f"There is no advisory specific to {label}. This is the general advice found for your crop."
    return "This is the general advice found for your crop." if specific else ""


def format_location_notes(
    results: List[Dict[str, Any]],
    place: Optional[Place] = None,
    named_place: str = "",
    unmatched: bool = False,
    lead: str = "",
    place_pending: bool = False,
) -> List[str]:
    """Lines telling the agent how each advisory relates to the farmer's place, and what to say about it.

    `lead` (the season / sowing stage sentence) goes after the place sentence. While the place is still to be
    asked (`place_pending`), nothing is said about the place: it is not known yet, so the advice is not "general"."""
    place = place or Place()
    where = "" if place_pending else location_opening(results, place, named_place, unmatched)
    if where and lead and not (place.label or named_place):
        # No place given (the farmer skipped it): say the stage first, then that it is not specific to their area.
        where, lead = lead, "It is not specific to your area."
    opening = f"{where} {lead.replace('This advice is', 'It is', 1)}" if where and lead else where or lead
    if not opening:
        return []
    lines = [
        "> Open your answer with this as the very first line (translate it to the farmer's language, "
        f"keep the places exactly as written): \"{opening}\" Then give the advice. Give only what the advisory text "
        "above says; do not add steps or details from your own knowledge."
    ]
    # Per-advisory place lines only when the farmer gave a place: with none, there is nothing to compare against.
    if place_pending or not (place.label or named_place) or not any(not r.get("location_national", True) for r in results):
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
            lines.append(f"> '{title}' is not for the farmer's place: present it as general advice.")
    return lines


def stage_opening(crop: str, season: str, days: Optional[int], days_matched: bool) -> str:
    """Sentence saying the advice fits the season and sowing stage the farmer gave ('' when they gave neither)."""
    subject = " ".join(part for part in (season, (crop or "").lower(), "crop") if part)
    if days is not None and days_matched:
        return f"This advice is for your {subject} at {days} days after sowing."
    return f"This advice is for your {subject}." if season else ""


def format_search_notes(
    search_ctx: Dict[str, str],
    days_after_sowing: Optional[int],
    location: str = "",
    place: Optional[Place] = None,
    results: Optional[List[Dict[str, Any]]] = None,
    season: str = "",
    crop: str = "",
    place_pending: bool = False,
) -> str:
    """Notes for the agent about how the search was applied, with what to tell the farmer."""
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    days_matched = str(search_ctx.get("days_matched") or "").strip().lower() == "true"
    facts = [f"advisory_has_day_range={'yes' if day_range else 'no'}"]
    if days_after_sowing is not None:
        facts.append(f"days_after_sowing_matched={'yes' if days_matched else 'no'}")
    lines = ["> Search notes: " + "; ".join(facts)]
    place = place or Place()
    unmatched = bool(location) and not place.known
    lead = stage_opening(crop, season, days_after_sowing, day_range and days_matched)
    lines.extend(
        format_location_notes(
            results or [], place, location if unmatched else "", unmatched=unmatched, lead=lead, place_pending=place_pending
        )
    )
    if days_after_sowing is not None and (not day_range or not days_matched):
        lines.append(
            f"> The farmer did give the days since sowing ({days_after_sowing}), but this advisory has no guidance "
            "for that sowing stage. Do not say the farmer did not give the days. Say briefly that this is the "
            "general advice for the crop and season."
        )
    elif days_after_sowing is None and not day_range:
        lines.append("> This advice is general for the crop and not tied to a sowing stage. Say so briefly.")
    return "\n".join(lines)


POLITE_ASK = (
    "Be polite and warm: start the question with \"Could you please tell me\" (in the farmer's language). Never "
    "say \"I can only answer after you tell me\", \"I need\", \"you must\", \"before I can answer\" or any "
    "wording that sounds like a condition or an order; do not open with the crop or the topic."
)


def _join_names(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _followup(markers: str, situation: str, ask: str, then: str) -> str:
    """Tells the agent to give the advice above now and end the answer with ONE follow-up question."""
    return (
        f"{PENDING_MARKER}{markers}\n> Follow-up: {situation} Give the advice above now. After "
        f"the Source line, end your answer with ONE short question {ask}, in the farmer's language; it is your only "
        f"follow-up question. {then} If the farmer does not answer it, do not ask it again."
    )


_DETAIL_QUESTIONS = {
    "season": "which season you are growing your crop in",
    "days": "how many days ago you sowed it",
    "place": "which state and district you are farming in",
}
_DETAIL_ARGS = {"season": "`season`", "days": "`days_after_sowing`", "place": "`state` and `district` (in English)"}
_DETAIL_NAMES = {"season": "the season", "days": "the sowing days", "place": "their state and district"}


def format_details_followup(
    seasons: List[str], need_season: bool, need_days: bool, need_place: bool = False, restate: str = ""
) -> str:
    """ONE follow-up asking every missing detail the advisories depend on: the season, the sowing days and/or the
    farmer's place (state and district), all in the same question."""
    asked = [key for key, need in (("season", need_season), ("days", need_days), ("place", need_place)) if need]
    phrases = ["how many days ago you sowed your crop"] if asked == ["days"] else [_DETAIL_QUESTIONS[k] for k in asked]
    example = "Could you please tell me " + (
        phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + ", and " + phrases[-1]
    ) + "?"
    # Name the seasons only when there is a real choice. With a single season, saying so makes the question
    # read as if that season were expected, and the farmer may well be in another one.
    seasons_note = (
        f" The advisories found cover the {_join_names(seasons)} seasons; you may name them in a plain sentence "
        "as the choices."
        if need_season and len(seasons) > 1
        else ""
    )
    season_rules = (
        " Do not suggest which season it is: the farmer decides. Write it as normal sentences: never put season "
        "names in brackets or in a list."
        if need_season
        else ""
    )
    differs = " or ".join((["season"] if need_season else []) + (["sowing stage"] if need_days else []))
    reasons = ([f"differ by {differs}"] if differs else []) + (["are meant for particular places"] if need_place else [])
    how = (
        f"Give the advice as general advice for the crop, and do not say which {differs} it is for."
        if differs
        else "Do not say whether the advice is for their place or call it general advice."
    )
    return _followup(
        (SEASON_MARKER if differs else "") + (LOCATION_MARKER if need_place else ""),
        f"These advisories {' and '.join(reasons)}, and the farmer has not given "
        f"{_join_names([_DETAIL_NAMES[k] for k in asked])}. {how}",
        "asking " + _join_names([_DETAIL_QUESTIONS[k] for k in asked]) + (", all in that one question" if len(asked) > 1 else ""),
        POLITE_ASK + f" Example (use the farmer's crop name instead of \"your crop\" and translate it): \"{example}\"."
        + seasons_note + season_rules + " Never split it into separate questions. When they answer, call "
        f"`search_advisory` again with the same `query` and `crop`{restate} plus whatever they gave of "
        + _join_names([_DETAIL_ARGS[k] for k in asked]) + ". Use what they give; do not ask again for anything "
        "they leave out.",
    )


def format_already_asked(missing: List[str]) -> str:
    """Note when the farmer was already asked for details and left some out (or gave a place we could not use)."""
    names = _join_names([_DETAIL_NAMES[k] for k in missing])
    return (
        f"> Already asked: the farmer was asked for {names} in the previous message and did not give a usable answer. "
        f"Do not ask for {names} again, in any wording, and do not tell the farmer their place was not found unless "
        "the notes above say so. If you end with a follow-up question, it must be about something else."
    )


def format_season_followup(season: str, subject: str, seasons: List[str]) -> str:
    """Follow-up when no advisory exists for the farmer's season: general advice, then offer the seasons that do."""
    return _followup(
        SEASON_MARKER,
        f"No advisory is available for the {season} season for {subject}; the advice above is from the "
        f"{' / '.join(seasons)} season advisories, so give it as general advice. Open your answer with one short "
        "sentence saying so (for example "
        f"\"There is no advisory specific to the {season} season for {subject}; here is the general advice found.\").",
        f"asking whether they would like the advice for the {' or '.join(seasons)} season",
        "If they say yes, call `search_advisory` again with the same `query`, `crop` and that `season`.",
    )


def format_crop_followup(crops: List[str]) -> str:
    """Follow-up when no crop was given and the advisories found are about several crops: ask which one."""
    return _followup(
        CROP_MARKER,
        f"The farmer has not said which crop, and these advisories are for different crops ({_join_names(crops)}). "
        "Give it as general advice: keep the answer short and name the crop each point is for.",
        "asking which crop they are asking about",
        POLITE_ASK + " When they answer, call `search_advisory` again with the same `query` plus `crop` and "
        "`crop_words`.",
    )


async def _post_search(payload: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], str]:
    """POST the search to KP; returns (response data, error message for the agent)."""
    if not knowledge_provider.search_url():
        logger.error("KNOWLEDGE_PROVIDER_URL is not set")
        return None, "Advisory search service is not configured. Please try again later."
    try:
        return await knowledge_provider.search(payload), ""
    except knowledge_provider.KnowledgeProviderError as exc:
        logger.error("Advisory search failed: %s", exc)
    except httpx.HTTPError as exc:
        logger.error("Advisory search request failed: %s", exc)
    return None, "Advisory search service is unavailable. Please try again later."


def _log_results(
    results: List[Dict[str, Any]],
    search_ctx: Dict[str, str],
    status: str,
    **extra: Any,
) -> None:
    lf_update_current_observation(
        metadata={
            "tool": "advisory.search",
            "category": KNOWLEDGE_ADVISORY_CATEGORY,
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
    """One KP search. Returns (results or None on a transport error, search context, status, message/error)."""
    payload = build_advisory_search_payload(
        query=query,
        crop=crop,
        days_after_sowing=days,
        season=season,
        top_k=top_k,
        state_lgd_code=place.state_code if place else None,
        district_lgd_code=place.district_code if place else None,
    )
    lf_update_current_observation(
        input={"query": query, "crop": crop or None, "days_after_sowing": days, "season": season or None, "top_k": top_k,
               "state_lgd_code": place.state_code if place else None,
               "district_lgd_code": place.district_code if place else None},
        metadata={"tool": "advisory.search", "category": KNOWLEDGE_ADVISORY_CATEGORY},
    )
    data, error = await _post_search(payload)
    if data is None:
        return None, {}, "", error
    results, search_ctx = parse_advisory_response(data)
    status = str(search_ctx.get("status") or "").strip().lower()
    message = str(search_ctx.get("message") or "").strip()
    _log_results(results, search_ctx, status, requery=requery)
    return results, search_ctx, status, message


def _farmer_place(
    ctx: RunContext[FarmerContext],
    state: Optional[str],
    district: Optional[str],
    location: Optional[str],
) -> tuple[Place, str]:
    """(place, named place we could not match) from what the farmer said while this question was being worked on.

    The farmer's own words win over what the model passes. The model may not carry a place over from an earlier
    question in the chat, but a place the farmer gave in an earlier turn of this same question still counts."""
    passed = [_clean_param(state), _clean_param(district), _clean_param(location)]
    from_args = resolve_place(*passed)
    if not getattr(ctx, "messages", None):  # nothing to check against (direct calls, tests)
        return from_args, "" if from_args.known else passed[2]

    chain = answer_chain(ctx)
    for text, answers_place in reversed(chain):  # newest first
        from_message = resolve_place(text, allow_short=answers_place)
        if from_message.known:
            if from_args.known and from_args != from_message:
                logger.info("search_advisory: using the place the farmer said, not %r", from_args.label)
            return from_message, ""
    if from_args.known and any(farmer_wrote(ctx, value) for value in passed if value):
        return from_args, ""
    if from_args.known:
        logger.info("search_advisory: ignoring place %r the farmer did not give", from_args.label)
    # A place the farmer typed (as the answer to our question, or in their message) that the lookup does not know.
    for text, answers_place in reversed(chain):
        leftover = place_words(text) if answers_place else ""
        if leftover:
            return Place(), leftover[:60]
    if passed[2] and farmer_wrote(ctx, passed[2]):
        return Place(), passed[2]
    return Place(), ""


@observe(name="tool:search_advisory", as_type="tool")
async def search_advisory(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: Optional[str] = None,
    days_after_sowing: Optional[int] = None,
    season: Optional[str] = None,
    location: Optional[str] = None,
    top_k: int = 5,
    state: Optional[str] = None,
    district: Optional[str] = None,
    crop_words: Optional[str] = None,
    image_url: Optional[str] = None,
) -> str:
    """
    Search farming advisories: crop pests, diseases, symptoms, crop and seed practices, harvesting, and
    livestock (cattle, buffalo, goat, poultry ...) health and care. Also identifies a pest or disease from
    an uploaded crop photo.

    Use this for every crop, pest, disease or livestock question, such as "aphids in wheat", "when to
    harvest sorghum" or "my cow has fever". Search straight away; do not ask the farmer anything first,
    not even the crop. Do NOT use it for government scheme questions (use search_schemes) or for weather,
    mandi prices or status checks.

    The tool always returns advice. If it depends on the crop, the season, the sowing stage or the farmer's
    place and the farmer has not said it, the tool gives it as general advice and tells you to end your answer
    with ONE follow-up question; when the farmer answers it, call again for the specific advice.

    Args:
        query: The farming problem in *English*, 2-5 words, including the crop or animal (required)
        crop: The crop in English (for example "Wheat"), only if the farmer named it in this question.
            Leave empty for livestock questions and for a crop question that names no crop.
        days_after_sowing: Whole number of days since the farmer sowed the crop, if the farmer said it.
        season: Rabi, Kharif or Zaid, if the farmer said it (also in the first message).
        location: A place the farmer mentioned that is not clearly a state or district (a village, a town),
            if any. It is only mentioned in the answer.
        top_k: Maximum number of advisories to return (default: 5)
        state: The farmer's state in English (for example "Uttar Pradesh"), only if the farmer said it.
        district: The farmer's district in English (for example "Agra"), only if the farmer said it.
        crop_words: The farmer's own words naming the crop, exactly as written in their message for this
            question (any language), for example "गेहूं". The crop must come from the farmer's current
            question, never from an earlier question in the chat.
        image_url: The URL of a crop photo the farmer uploaded. When given, the photo is analysed for a pest
            or disease (National Pest Surveillance System) and the other arguments are not used.

    Returns:
        Advisory text with a "Search notes" line, sometimes followed by a "Follow-up" line telling you which
        one question to end the answer with; or a message saying nothing was found; or, for a photo, the NPSS
        identification.
    """
    if image_url and image_url.strip():
        return await analyze_crop_image(ctx, image_url)
    query = to_english(query)
    days = _clean_days(days_after_sowing)
    season_value = normalize_season(season)
    # Hard guards: details only the farmer can give are ignored when the conversation does not show them.
    if season_value and not farmer_gave_season(ctx):
        logger.info("search_advisory: ignoring season %r the farmer did not give", season_value)
        season_value = None
    if days is not None and not farmer_gave_days(ctx):
        logger.info("search_advisory: ignoring days_after_sowing %r the farmer did not give", days)
        days = None
    # The farmer's place comes only from the farmer. Their own words win over what the model passes, and the
    # model may not carry a place over from earlier in the chat.
    place, named_place = _farmer_place(ctx, state, district, location)
    location_value = named_place
    # Option C: a crop the model passes must be in the farmer's words for this question. No crop is allowed
    # (livestock); a crop question without one is answered for each crop found, with a follow-up asking which.
    raw_crop = _clean_param(crop)
    crop_hint = to_english(raw_crop)
    words = _clean_param(crop_words)
    if crop_hint and not (farmer_named_crop(ctx, crop_hint, words) or farmer_named_crop(ctx, raw_crop, words)):
        logger.info(
            "search_advisory: crop %r is not in the farmer's words for this question; turns (* = ended with a "
            "search_advisory question): %s",
            crop_hint, describe_turns(ctx, crop_hint),
        )
        said = (answer_chain(ctx) or [("", False)])[-1][0].strip()
        raise ModelRetry(
            f"The crop `{crop_hint}` is not in the farmer's words for this question. Their message is: \"{said}\". "
            "Read it again. If it names a crop (in any language), call search_advisory again with that crop as "
            "`crop` and the farmer's exact words for it as `crop_words`; do not ask which crop. Only if it names "
            "no crop at all, call search_advisory again without `crop`."
        )
    # The farmer answered the crop question with only the crop: search the crop's general advisory.
    if crop_hint and _same_words(query, crop_hint):
        query = f"{crop_hint} crop advisory"
    limit = _clean_top_k(top_k) or 5

    results, search_ctx, status, message = await _run_search(
        ctx, query, days, season_value, top_k=limit, place=place
    )
    if results is None:
        return message  # transport error text for the agent
    if not results:
        return _no_results_message(query, status, message)

    chunks, crop_name = relevant_for_crop(results, crop_hint, query)
    found_crops = list(dict.fromkeys(r["crop"] for r in chunks if r.get("crop")))
    if not crop_name and len(found_crops) > 1:
        followup = "" if asked_crop_in_previous_turn(ctx) else format_crop_followup(found_crops)
        return _answer(chunks, search_ctx, query, limit, days, named_place, place, followup) + await agromet_note(ctx, query=query)
    coffee_pdf = [r for r in chunks if is_coffee_pdf(r)]
    if coffee_pdf:
        # Generic answer: a coffee PDF is not tied to a season, sowing stage or place, so ask the farmer nothing.
        # Chunk-level season/place are blanked so no re-query, suggestion or place sentence is triggered.
        generic = [{**r, "seasons": [], "location_national": True} for r in coffee_pdf]
        return _answer(generic, {**search_ctx, "has_day_range": "false"}, query, limit, None, "") + await agromet_note(ctx, query=query)
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
        followup = format_season_followup(season_value, crop_name or f"`{query}`", seasons)
        return _answer(chunks, search_ctx, query, limit, days, named_place, place, followup) + await agromet_note(ctx, query=query)

    # One follow-up per answer, each asked once: first the season (when they gave none) and the days whenever the
    # advisories carry that detail; then the farmer's place, only when the advisories we answer from are tied to places.
    seasons = [] if season_value else explicit_seasons(season_ok)
    season_ok = for_days(season_ok, days)  # drop advisories for another sowing stage (e.g. harrowing at 15 days)
    day_range = str(search_ctx.get("has_day_range") or "").strip().lower() == "true"
    need_season, need_days = bool(seasons), day_range and days is None
    top = _top_results(season_ok, limit)
    place_matters = any(not r.get("location_national", True) for r in top)
    need_place = place_matters and not place.known and not named_place
    # ONE follow-up for everything missing (season, sowing days, place), asked once: after it, answer with what
    # the farmer gave and ask nothing more about these details.
    followup = ""
    asked_before = asked_season_in_previous_turn(ctx) or asked_location_in_previous_turn(ctx)
    if (need_season or need_days or need_place) and not asked_before:
        restate = (f", `season`=\"{season_value}\"" if season_value else "") + (
            f", `days_after_sowing`={days}" if days is not None else ""
        )
        followup = format_details_followup(seasons, need_season, need_days, need_place, restate)
    elif asked_before:
        # Asked once already: whatever is still missing (or a place we could not match) is not asked again.
        missing = [k for k, need in (("season", need_season), ("days", need_days), ("place", place_matters and not place.known)) if need]
        followup = format_already_asked(missing) if missing else ""
    return _answer(
        season_ok, search_ctx, query, limit, days, named_place, place, followup, season=season_value or "", crop=crop_name
    ) + await agromet_note(ctx, query=query)


def _answer(
    results: List[Dict[str, Any]],
    search_ctx: Dict[str, str],
    query: str,
    limit: int,
    days: Optional[int],
    location: str,
    place: Optional[Place] = None,
    followup: str = "",
    season: str = "",
    crop: str = "",
) -> str:
    """The advice and its search notes, then the follow-up question to end the answer with (if any)."""
    top = _top_results(results, limit)
    notes = format_search_notes(
        search_ctx, days, location=location, place=place, results=top, season=season, crop=crop,
        # Nothing about the place while it is still to be asked, or while the crop is not known yet.
        place_pending=LOCATION_MARKER in followup or CROP_MARKER in followup,
    )
    return (
        format_advisory_results(top, query)
        + "\n\n"
        + notes
        + (f"\n\n{followup}" if followup else "")
    )
