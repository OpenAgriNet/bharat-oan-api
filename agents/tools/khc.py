"""Kisan Help Center grievance creation and status through the Beckn provider."""

import difflib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
import yaml
from jinja2 import Template
from langfuse import observe
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.aif import _build_payload, _descriptor, _endpoint, _outcome_tag, _values
from agents.tools.kcc import _mobile
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.utils import get_logger, get_prompt, to_ascii_digits

logger = get_logger(__name__)
_MESSAGES_FILE = Path(__file__).resolve().parents[2] / "assets/prompts/skills/khc/messages.yaml"


@lru_cache(maxsize=1)
def _messages() -> dict:
    return yaml.safe_load(_MESSAGES_FILE.read_text(encoding="utf-8"))


def _msg(key: str, **context) -> str:
    """Render a tool message from assets/prompts/skills/khc/messages.yaml."""
    return Template(_messages()[key]).render(**context)


SOURCE = _msg("source")
_STATUS_ID_PROMPT = _msg("status_id_prompt")
_STATUS_OTP_PROMPT = _msg("status_otp_prompt")
_MASTER_FIELDS = ("stateId", "villageId", "scheme", "division", "problemCategory", "problemSubCategory")
_LANGUAGE_CODES = {
    "hindi": "hi", "english": "en", "marathi": "mr", "bengali": "bn",
    "gujarati": "gu", "kannada": "kn", "tamil": "ta", "telugu": "te",
    "assamese": "as", "malayalam": "ml",
}
_UNCONFIRMED = _msg("unconfirmed")


# Words that say nothing about the problem itself (the scheme name is removed separately).
_NOT_A_PROBLEM = {
    "grievance", "grivance", "complaint", "problem", "issue", "issues", "help", "register", "create", "raise",
    "file", "lodge", "scheme", "khc", "farmer", "want", "need", "kisan", "helpline", "center", "centre",
    "state", "district", "village", "please", "yes", "the", "and", "for", "with", "about", "regarding",
}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


# The farmer's own mention of KHC, tolerating "k.h.c.", "heliline" and "Help-Line"; not "khcx" or "centerpiece".
_KHC_NAME = re.compile(
    r"(?<![a-z])(?:k\.?\s?h\.?\s?c\.?(?![a-z])|kisan\s*hel[a-z]*[\s-]*(?:line)?\s*cent(?:er|re)(?![a-z]))",
    re.I,
)


def _turns(messages) -> list[tuple[str, str]]:
    """The chat in order as ('farmer', text) and ('bot', text); tool calls and returns are left out."""
    turns: list[tuple[str, str]] = []
    for message in messages or []:
        for part in getattr(message, "parts", None) or []:
            kind = getattr(part, "part_kind", "")
            if kind == "user-prompt":
                content = part.content if isinstance(part.content, str) else " ".join(
                    item for item in part.content if isinstance(item, str)
                )
                if content.startswith("**Conversation**\n\n") and "\n\n---\n\n" in content:
                    content = content.split("\n\n---\n\n", 1)[1]
                found = re.search(r'\*\*User:\*\* "(.*?)"(?:\n\*\*|\n[A-Z]|\Z)', content, re.S)
                turns.append(("farmer", found.group(1) if found else content))
            elif kind == "text":
                turns.append(("bot", str(part.content)))
    return turns


def khc_service_note(messages, current_query: str = "") -> str:
    """Always provide status routing, plus a scoped reminder of an earlier KHC choice.

    Routing must be available before lazy-loaded workflow rules. Keep explicit KHC choices
    across follow-up turns without treating them as a permanent choice for the whole chat.
    Dynamic system prompts run before the current user message is appended to history.
    """
    routing = "\n\n" + get_prompt("skills/khc/grievances/status_routing", context={
        "status_id_prompt": _STATUS_ID_PROMPT,
        "status_otp_prompt": _STATUS_OTP_PROMPT,
    })
    farmer_said = [text for who, text in _turns(messages) if who == "farmer"] + [current_query]
    if not any(_KHC_NAME.search(text) for text in farmer_said):
        return routing
    return routing + get_prompt("skills/khc/grievances/service_selected", context={})


def _asked_for_problem(text: str) -> bool:
    low = text.lower()
    return "own words" in low and any(word in low for word in ("problem", "complaint", "issue"))


def _resolve_concern(ctx, concern: str, scheme: str) -> str | None:
    """The concern to search with, or None when the farmer has not described a problem yet.

    Use the latest ASCII reply to the problem question intact, rather than a model summary that
    may drop category-identifying words. Recover that reply after later questions if the model
    omits it. A concern made up by the model is refused unless it uses words the farmer wrote.
    Non-ASCII replies still accept an English translation, as do calls with no history to compare.
    """
    turns = _turns(getattr(ctx, "messages", None))
    farmer = [index for index, (who, _) in enumerate(turns) if who == "farmer"]
    if not farmer:
        return concern
    reply = None
    for index in reversed(farmer):
        if index and turns[index - 1][0] == "bot" and _asked_for_problem(turns[index - 1][1]):
            reply = " ".join(turns[index][1].split())[:500]
            # ponytail: ASCII-only reuse preserves translations; language detection can extend it.
            if index == farmer[-1] and reply.isascii():
                return reply
            break
    said = " ".join(turns[index][1] for index in farmer)
    foreign = bool(re.search(r"[^\x00-\x7f]", said))
    ignore = _words(scheme) | _NOT_A_PROBLEM
    theirs = _words(said) - ignore
    if concern and (foreign or any(
        word in theirs or difflib.get_close_matches(word, theirs, n=1, cutoff=0.8)
        for word in _words(concern) - ignore
    )):
        return concern
    if reply is not None:
        return reply
    return concern if foreign else None


def _loose(text: str) -> str:
    """Lowercase letters and digits only, so 'Uttar Pradesh' and 'uttarpradesh' compare equal."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _chain(match: dict) -> str:
    """'Sub-district X, District Y, State Z' for the levels above the match."""
    names = [(match.get(key) or {}).get("name") for key in ("subDistrict", "district", "state")]
    return ", ".join(name for name in names if name and name != match.get("name"))


def _ids(match: dict) -> str:
    keys = (("state", "stateId"), ("district", "districtId"), ("subDistrict", "subDistrictId"))
    parts = [f"{label}={match[key]['id']}" for key, label in keys if match.get(key)]
    if match.get("level") == "village":
        parts.append(f"villageId={match['id']}")
    return ", ".join(parts)


def _render_location(fields: dict[str, str], place: str) -> str:
    matches = json.loads(fields.get("matches", "[]"))
    district = json.loads(fields.get("district", "null"))
    district_id = district.get("id", "<districtId>") if isinstance(district, dict) else "<districtId>"
    needs = fields.get("needs", "").strip('"')
    if not isinstance(matches, list) or not all(isinstance(m, dict) for m in matches):
        raise ValueError("Invalid location matches")
    if needs == "state" and matches:
        return _msg("location_states", names="\n".join(f"- {m['name']}" for m in matches))
    if needs == "district" and matches:
        return _msg("location_districts", names="\n".join(f"- {m['name']}" for m in matches))
    if not matches:
        ask = {
            "state": _msg("location_ask_state"),
            "district": _msg("location_ask_district"),
            "geocode": _msg("location_ask_geocode", district_id=district_id),
        }.get(needs, _msg("location_ask_place"))
        return _msg("location_not_found", place=place, ask=ask)
    # The farmer was asked for a village, so a village match beats a district or sub-district of the same name.
    villages = [m for m in matches if m.get("level") == "village"]
    matches = villages or matches
    lines = []
    for index, match in enumerate(matches, 1):
        level = match.get("level", "place").replace("-", " ")
        chain = _chain(match)
        lines.append(f"{index}. {level} {match['name']}" + (f" - {chain}" if chain else "") + f" [{_ids(match)}]")
    listing = "\n".join(lines)
    if not villages:
        return _msg("location_not_a_village", place=place, listing=listing)
    if len(matches) == 1:
        return _msg("location_one", listing=listing)
    # Every row already carries its full chain, so one pick of a "Sub-district, District" option is enough.
    one_district = len({(m.get("district") or {}).get("name") for m in matches}) == 1
    options = "\n".join(
        f"- {m['name']}: {(m.get('subDistrict') or {}).get('name', '?')} sub-district"
        + ("" if one_district else f", {(m.get('district') or {}).get('name', '?')} district")
        for m in matches
    )
    cut = int(fields.get("total", "0") or 0) > len(matches)
    return _msg("location_several", place=place, options=options, listing=listing, cut=cut)


def _render_tree(fields: dict[str, str], scheme_name: str = "", concern: str = "", said: str = "") -> str:
    needs = fields.get("needs", "").strip('"')
    if needs == "state":
        names = "\n".join(f"- {m['name']}" for m in json.loads(fields.get("matches", "[]")))
        return _msg("tree_states", names=names)
    state = json.loads(fields.get("state", "null"))
    if not isinstance(state, dict):
        raise ValueError("Missing state")
    state_info = {"state_name": state["name"], "state_id": state["id"]}
    if needs == "scheme":
        schemes = json.loads(fields.get("schemes", "[]"))
        lines = "\n".join(f"- {s['name']} [schemeId={s['id']}, divisionId={s['division']}]" for s in schemes)
        match = fields.get("match", "").strip('"')
        # No names for "unnamed" on purpose: a list shown before the farmer is asked becomes a menu.
        key = {"none": "tree_scheme_none", "unnamed": "tree_scheme_unnamed"}.get(match, "tree_scheme_many")
        if match == "unnamed":
            # The model dropped the scheme the farmer already said: name the latest one so it cannot ask again.
            latest = {
                s["name"]: max(m.start() for m in found)
                for s in schemes
                if (found := list(re.finditer(rf"(?<!\w){re.escape(s['name'])}(?!\w)", said, re.I)))
            }
            if latest:
                scheme_name, key = max(latest, key=latest.get), "tree_scheme_already_named"
        return _msg(key, scheme_name=scheme_name, lines=lines, **state_info)
    scheme = json.loads(fields.get("scheme", "null"))
    scheme_info = {**state_info, "scheme_name": scheme["name"], "scheme_id": scheme["id"], "division": scheme["division"]}
    if not concern:
        # No category names here on purpose: if the model sees a list before the farmer has described
        # the problem, it shows the list instead of asking for the problem and matching it.
        return _msg("tree_no_concern", **scheme_info)
    categories = json.loads(fields.get("categories", "[]"))
    if not isinstance(categories, list) or not categories:
        raise ValueError("Missing KHC grievance catalogue")
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("subCategories"), list):
            raise ValueError("Invalid KHC category")
        for option in [category, *category["subCategories"]]:
            if (
                not isinstance(option, dict) or type(option.get("id")) is not int
                or not 0 < option["id"] <= 9007199254740991
                or not isinstance(option.get("name"), str) or not option["name"].strip()
            ):
                raise ValueError("Invalid KHC grievance option")
    return _msg("tree_catalogue", concern=concern, catalogue=json.dumps(categories, ensure_ascii=False), **scheme_info)


async def _call(ctx: RunContext[FarmerContext], action: str, **values: str) -> str:
    failure = _UNCONFIRMED if (
        action == "init" and values.get("request_type", "create_grievance") == "create_grievance"
    ) else _msg("service_unavailable")
    mobile = values.get("farmerMobileNumber")
    transaction_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"khc:{ctx.deps.session_id}:{mobile}")) if mobile else str(uuid.uuid4())
    payload = _build_payload(action, transaction_id, ctx, **values)
    order = payload["message"]["order"]
    order["provider"]["id"] = "khc-agri"
    order["items"][0]["id"] = "khc-grievance"
    try:
        endpoint = _endpoint(action)
    except ValueError:
        logger.error("khc.endpoint_not_configured")
        return _msg("service_not_configured")

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(endpoint, json=payload)
        if not 200 <= response.status_code < 300:
            logger.error("khc.request_failed action=%s status=%s", action, response.status_code)
            return failure
        envelope = response.json()
        if not isinstance(envelope, dict):
            raise ValueError("Invalid KHC network envelope")
        tag = _outcome_tag(envelope, action)
        code, message = _descriptor(tag)
        fields = _values(tag)
        expected = (
            "status_otp_sent" if values.get("request_type") == "get_status_otp" else "grievance_status"
        ) if action == "status" else {
            "get_otp": "otp_sent", "verify_otp": "otp_verified",
            "get_reference": "khc_reference", "create_grievance": "grievance_created",
            "find_location": "khc_location", "grievance_tree": "khc_grievance_tree",
        }.get(values.get("request_type"), "grievance_created")
        if code != expected:
            if (
                code == "otp_required"
                and action == "init"
                and values.get("request_type") == "create_grievance"
            ):
                return _msg("reverify_mobile", mobile=values.get("farmerMobileNumber"))
            if code in (
                "not_found", "invalid_request", "submission_unconfirmed", "upstream_error",
                "configuration_error", "otp_required", "invalid_otp", "invalid_mobile", "rate_limited",
            ):
                return message or failure
            raise ValueError("Missing KHC outcome tag")
        if code == "status_otp_sent":
            return _msg("status_otp_sent", status_otp_prompt=_STATUS_OTP_PROMPT)
        if code == "otp_sent":
            return _msg("otp_sent")
        if code == "otp_verified":
            if fields.get("isRegistered") not in ("true", "false"):
                raise ValueError("Missing KHC registration state")
            return _msg("otp_verified", registered=fields["isRegistered"] == "true")
        if code == "khc_location":
            return f"{_render_location(fields, values.get('name', ''))}\n{SOURCE}"
        if code == "khc_grievance_tree":
            said = " ".join(text for who, text in _turns(getattr(ctx, "messages", None)) if who == "farmer")
            return f"{_render_tree(fields, values.get('scheme_name', ''), values.get('concern', ''), said)}\n{SOURCE}"
        if code == "khc_reference":
            options = json.loads(fields.get("options", "null"))
            if not isinstance(options, list) or not all(isinstance(option, dict) for option in options):
                raise ValueError("Invalid reference options")
            reference = _msg(
                "reference_options", reference_type=values.get("reference_type"),
                options=json.dumps(options, ensure_ascii=False),
                hold_back=values.get("reference_type") in ("categories", "sub-categories"),
                total=fields.get("total", ""), page=fields.get("page", ""),
            )
            return f"{reference}\n{SOURCE}"
        if not fields.get("grievanceNo") or (action == "status" and (
            not fields.get("currentStatus") or fields["grievanceNo"] != values.get("grievanceNo")
        )):
            raise ValueError("Missing or mismatched KHC grievance details")
        labels = _messages()["labels"]
        lines = [message, f"{labels['grievance_number']}: {fields['grievanceNo']}"]
        for field in ("currentStatus", "currentLevel", "createdAt"):
            if fields.get(field):
                lines.append(f"{labels[field]}: {fields[field]}")
        if action == "status":
            if fields.get("externalReferenceNo"):
                lines.append(f"{labels['externalReferenceNo']}: {fields['externalReferenceNo']}")
            for field, expected_type in (("address", dict), ("escalationHistory", list)):
                if fields.get(field):
                    details = json.loads(fields[field])
                    if details is None:
                        continue
                    if not isinstance(details, expected_type) or (
                        isinstance(details, list) and not all(isinstance(item, dict) for item in details)
                    ):
                        raise ValueError(f"Invalid KHC {field}")
                    lines.append(f"{labels[field]}: {json.dumps(details, ensure_ascii=False)}")
        lines.append(SOURCE)
        return "\n".join(lines)
    except (httpx.RequestError, ValueError, TypeError, AttributeError, KeyError):
        logger.error("khc.request_or_response_failed action=%s", action)
        return failure


@observe(name="tool:initiate_khc_otp", as_type="tool")
async def initiate_khc_otp(ctx: RunContext[FarmerContext], mobile_number: str = "") -> str:
    """Send a KHC OTP before grievance registration, to a mobile supplied by the farmer.

    Ask for the farmer's mobile if absent. Do not send OTPs for a status-only lookup.
    Call this only after scheme, location, concern, mapped sub-category, and farmer
    confirmation are complete for the current grievance.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return _msg("mobile_needed")
    if not ctx.deps.session_id:
        return _msg("registration_needs_session")
    return await _call(ctx, "init", request_type="get_otp", farmerMobileNumber=mobile)


@observe(name="tool:verify_khc_otp", as_type="tool", capture_input=False, capture_output=False)
async def verify_khc_otp(
    ctx: RunContext[FarmerContext], mobile_number: str = "", otp: str = "",
) -> str:
    """Verify the KHC OTP received by the farmer. Sending must happen first.

    Use the same mobile number as initiate_khc_otp. The result identifies new versus
    existing farmers. Never invent OTPs or report verification without a tool result.
    While waiting for OTP, call this tool for a six-digit reply; do not ask the farmer
    to repeat a correctly formatted OTP without attempting verification. Preserve leading
    zeros. Only the tool result can establish acceptance, rejection or service failure.
    Grievance details and the mapped sub-category must already be confirmed. Only after successful
    verification, use isRegistered to decide: existing farmers submit directly; new farmers supply
    any missing name and gender before submission. Do not collect those fields before verification.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return _msg("mobile_needed")
    code = to_ascii_digits(str(otp or "")).strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        return _msg("otp_needed")
    if not ctx.deps.session_id:
        return _msg("registration_needs_session")
    return await _call(ctx, "init", request_type="verify_otp", farmerMobileNumber=mobile, otp=code)


@observe(name="tool:find_khc_location", as_type="tool")
async def find_khc_location(
    ctx: RunContext[FarmerContext], place: str, state: str = "", district: str = "",
    sub_district: str = "",
) -> str:
    """Find where a place is: its state, district, sub-district and village IDs, in one call.

    Use this first for the KHC grievance location, with the village or place name the
    farmer gave (ask state, then district, then village). Pass the state and district. When the farmer has
    picked a sub-district from a list, call again with the same village as place and that sub-district
    as sub_district; it then returns exactly one place. Names
    may have spacing, case or small spelling differences. A place that is a district also
    matches. If nothing matches, the result says what to ask next. Do not use for status.
    """
    place, state, district = place.strip(), state.strip(), district.strip()
    sub_district = sub_district.strip()
    if not place or len(place) > 200 or max(len(state), len(district), len(sub_district)) > 100:
        return _msg("place_needed")
    if state and _loose(place) == _loose(state):
        return _msg("place_is_state")
    if district and _loose(place) == _loose(district):
        return _msg("place_is_district")
    return await _call(
        ctx, "init", request_type="find_location", name=place, state_name=state, district_name=district,
        sub_district_name=sub_district,
    )


@observe(name="tool:get_khc_grievance_options", as_type="tool")
async def get_khc_grievance_options(
    ctx: RunContext[FarmerContext], state: str, scheme: str = "", concern: str = "", category: str = "",
) -> str:
    """Get a state's KHC grievance options in one call.

    Without a scheme it lists the state's schemes. With the scheme the farmer named
    (AIF, A.I.F. and the full name all work) it needs concern: the farmer's original complaint
    (up to 500 characters), translated to English only if needed. Do not shorten it or drop
    category-identifying words. Call it only after the scheme, state, district and
    village are done and the farmer has described the problem. Without concern it returns no
    categories and no next step. With concern it returns the API-backed category/sub-category
    catalogue for you to match semantically. Use a clear match without showing a list. Ask only
    when the meaning is ambiguous, showing categories if none fits or just the matched category's
    sub-categories if only the sub-category is unclear. After a category pick, call again with the
    same concern and category=<their pick> to narrow the catalogue. Use only returned IDs and
    preserve parent-child relationships. Show the issue and mapped sub-category in the confirmation summary.
    Prefer this over three separate lookup_khc_reference calls.
    """
    state, scheme, concern = state.strip(), scheme.strip(), " ".join(concern.split())
    category = category.strip()
    if not state:
        return _msg("options_need_state")
    if max(len(state), len(scheme), len(category)) > 100 or len(concern) > 500:
        return _msg("options_too_long")
    if scheme:
        resolved = _resolve_concern(ctx, concern, scheme)
        if resolved is None and concern:
            logger.info("khc.concern_refused session=%s concern_len=%d", ctx.deps.session_id, len(concern))
            return _msg("options_concern_refused")
        if resolved is not None and resolved != concern:
            logger.info("khc.concern_from_farmer_reply session=%s", ctx.deps.session_id)
            concern = resolved
    return await _call(
        ctx, "init", request_type="grievance_tree", state_name=state, scheme_name=scheme,
        concern=concern, category_name=category,
    )


@observe(name="tool:lookup_khc_reference", as_type="tool")
async def lookup_khc_reference(
    ctx: RunContext[FarmerContext], reference_type: str,
    parent_id: Optional[int] = None, name: str = "", page: int = 1,
) -> str:
    """Fallback lookup of KHC reference lists and IDs by name. Prefer find_khc_location (places) and
    get_khc_grievance_options (scheme, category, sub-category); use this only if they are unavailable
    or fail, or the farmer explicitly asks to see alternatives to a matched category/sub-category.

    Lists and parents: states (none), districts (stateId), sub-districts (districtId),
    villages (subDistrictId), schemes (stateId), categories (scheme),
    sub-categories (problemCategory). Take the scheme and division from the SAME row.
    Keep the grievance flow's order: ask the farmer, match what they said, and show a list of
    names only when nothing matched or several matched. Never guess an ID or a scheme.

    name is a case- and whitespace-insensitive substring filter on the name (uttarpradesh matches
    Uttar Pradesh), not a search over a complaint: never pass the complaint as name. Use name=""
    to see the options when a filter finds nothing, and read the remaining pages (50 per page)
    before saying nothing matches. Do not ask the farmer for IDs.
    """
    parents = {
        "states": None, "districts": "stateId", "sub-districts": "districtId",
        "villages": "subDistrictId", "schemes": "stateId", "categories": "scheme",
        "sub-categories": "problemCategory",
    }
    if reference_type not in parents or type(page) is not int or page <= 0:
        return _msg("lookup_invalid")
    if parents[reference_type] and (type(parent_id) is not int or not 0 < parent_id <= 9007199254740991):
        return _msg("lookup_parent_needed", parent=parents[reference_type], reference_type=reference_type)
    if len(name) > 200:
        return _msg("lookup_name_too_long")
    values = {"request_type": "get_reference", "reference_type": reference_type, "name": name, "page": str(page)}
    if parent_id is not None:
        values["parent_id"] = str(parent_id)
    return await _call(ctx, "init", **values)


@observe(name="tool:check_khc_grievance_status", as_type="tool", capture_input=False, capture_output=False)
async def check_khc_grievance_status(
    ctx: RunContext[FarmerContext], grievance_number: str = "", otp: str = "",
) -> str:
    """Send a status OTP, or verify it and retrieve KHC grievance status.

    Use only after KHC is identified as the service for this grievance. For a generic status
    request with no known scheme or service, ask which scheme or service first, not for a number.
    Named PM-Kisan, PMFBY and AIF grievances use their own status flows unless KHC was chosen.
    Once KHC is known, ask the farmer for their grievance number only if missing.

    First call without otp to send SMS to the mobile registered on the grievance.
    After successful sending, ask for the 6-digit OTP and call this same tool with
    the same grievance_number and the farmer's otp. Never use registration OTP tools.
    The OTP expires after 5 minutes and is single-use. Resend only when requested
    by calling without otp; this invalidates the previous OTP. Never retry automatically.
    Keep slashes and leading zeros intact, e.g. UP/231026/5 and OTP 012345.
    Grievance numbers must have two ASCII letters, exactly six digits in the middle and a positive numeric
    sequence separated by slashes, e.g. UP/231026/5 (an example, never a default).
    Do not interpret the middle section as a calendar date. Never truncate, pad or replace
    characters to make an invalid number pass; ask for the full ID from the SMS again.
    Surrounding whitespace and letter case are normalized. Ask for the full ID received by SMS on the registered
    mobile if missing or invalid. Announce sending only after the send call succeeds.
    Do not ask for a mobile, beneficiary ID or cookie. Never invent a number or OTP.
    A wrong OTP can be corrected until expiry; an expired/used OTP needs a fresh send.
    Explain ID and OTP failures based on the tool result, without guessing a cause.
    """
    number = to_ascii_digits(str(grievance_number or "")).strip()
    if len(number) > 100 or not re.fullmatch(r"[A-Za-z]{2}/[0-9]{6}/0*[1-9][0-9]*", number):
        reply = _STATUS_ID_PROMPT
        if number:
            parts = number.split("/")
            reason = (
                _msg("status_id_reason_digits", count=len(parts[1]))
                if len(parts) == 3 and re.fullmatch(r"[0-9]+", parts[1]) and len(parts[1]) != 6
                else _msg("status_id_reason_format")
            )
            reply = _msg("status_id_incorrect", reason=reason)
        return _msg("status_id_request", reply=reply)
    number = number.upper()
    if otp != "":
        code = to_ascii_digits(str(otp or "")).strip()
        if not re.fullmatch(r"[0-9]{6}", code):
            return _msg("status_otp_format")
        return await _call(ctx, "status", request_type="verify_status_otp", grievanceNo=number, otp=code)
    return await _call(ctx, "status", request_type="get_status_otp", grievanceNo=number)


@observe(name="tool:register_khc_grievance", as_type="tool")
async def register_khc_grievance(
    ctx: RunContext[FarmerContext],
    mobile_number: str = "",
    farmer_type: str = "",
    grievance_text: str = "",
    farmer_language: str = "",
    state_id: Optional[int] = None,
    village_id: Optional[int] = None,
    scheme_id: Optional[int] = None,
    division_id: Optional[int] = None,
    problem_category_id: Optional[int] = None,
    problem_subcategory_id: Optional[int] = None,
    farmer_name: str = "",
    gender: str = "",
    confirmed: bool = False,
) -> str:
    """Register a KHC grievance for a new or existing farmer, ONLY after explicit confirmation.

    First send and verify OTP with initiate_khc_otp / verify_khc_otp. Use the verified
    registration state for farmer_type (new/existing). Collect mobile, preferred
    language, location and grievance details. Resolve scheme/category/subcategory from
    the problem using the reference APIs; reuse scheme already provided and ask for
    scheme name only when missing. Never ask for internal IDs/codes from the farmer.
    Only after OTP verification reports isRegistered=false, collect any missing name and
    gender code for the new farmer. Existing farmers do not need these fields.
    All six numeric IDs MUST come from verified KHC master data, not LGD
    assumptions, memory, example requests or guesses. Resolve IDs using lookup_khc_reference.
    Reuse permitted browser location via reverse_geocode and KHC reference lookups
    when the farmer did not state a grievance location; otherwise ask for state/village.
    Summarize the details and obtain explicit confirmation before setting confirmed=True.
    farmer_language must use a supported KHC code such as hi or en; English language
    names such as Hindi or English are also normalized to codes before sending.
    gender uses M/F/O; Male/Female/Other are normalized for new farmers.
    The provider rejects creation without a verified mobile. Never retry automatically
    after an unconfirmed submission.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return _msg("mobile_needed")
    if farmer_type not in ("new", "existing"):
        return _msg("register_verify_first")
    if not grievance_text.strip() or len(grievance_text) > 5000:
        return _msg("register_grievance_text")
    farmer_language = _LANGUAGE_CODES.get(farmer_language.strip().lower(), farmer_language.strip().lower())
    if farmer_language not in _LANGUAGE_CODES.values():
        return _msg("register_language")
    gender = gender.strip().upper()
    gender = {"MALE": "M", "FEMALE": "F", "OTHER": "O"}.get(gender, gender)
    if farmer_type == "new":
        if not farmer_name.strip():
            return _msg("register_name")
        if gender not in ("M", "F", "O"):
            return _msg("register_gender")
    ids = (state_id, village_id, scheme_id, division_id, problem_category_id, problem_subcategory_id)
    if any(type(value) is not int or not 0 < value <= 9007199254740991 for value in ids):
        return _msg("register_ids")
    if not confirmed:
        return _msg("register_confirm")
    if not ctx.deps.session_id:
        logger.error("khc.registration_missing_session")
        return _msg("register_new_conversation")
    values = {
        "farmerMobileNumber": mobile, "farmerType": farmer_type,
        "farmerText": grievance_text.strip(), "farmerLanguage": farmer_language,
        **{field: str(value) for field, value in zip(_MASTER_FIELDS, ids)},
    }
    if farmer_type == "new":
        values.update(farmerName=farmer_name.strip(), gender=gender)
    reference = uuid.uuid5(uuid.NAMESPACE_URL, ctx.deps.session_id + ":" + json.dumps(values, sort_keys=True))
    values.update(externalReferenceNo=f"BV-{reference}", confirmed="true", request_type="create_grievance")
    return await _call(ctx, "init", **values)
