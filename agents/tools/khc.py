"""Kisan Help Center grievance creation and status through the Beckn provider."""

import json
import re
import uuid
from typing import Optional

import httpx
from langfuse import observe
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.aif import _build_payload, _descriptor, _endpoint, _outcome_tag, _values
from agents.tools.kcc import _mobile
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)
SOURCE = "**Source:** Kisan Helpline Center"
_MASTER_FIELDS = ("stateId", "villageId", "scheme", "division", "problemCategory", "problemSubCategory")
_LANGUAGE_CODES = {
    "hindi": "hi", "english": "en", "marathi": "mr", "bengali": "bn",
    "gujarati": "gu", "kannada": "kn", "tamil": "ta", "telugu": "te",
    "assamese": "as", "malayalam": "ml",
}
_UNCONFIRMED = (
    "KHC grievance submission was not confirmed. Do not submit again automatically; "
    "verify with KHC first."
)


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
        names = "\n".join(f"- {m['name']}" for m in matches)
        return (
            "Several or no states matched what the farmer said. Show these state names and ask "
            f"'Which state are you from?' (select by name):\n{names}"
        )
    if not matches:
        ask = {
            "state": "Ask 'Which state are you from?', then call this tool again with that state.",
            "district": "Ask 'Which district are you from?', then call this tool again with that district.",
            "geocode": (
                "Not found in this district's lists. Steps: (1) call forward_geocode and reverse_geocode on "
                "the place to fix the spelling, then call this tool again. (2) If still not found, call "
                f"lookup_khc_reference(reference_type=\"sub-districts\", parent_id={district_id}, name=\"\"), "
                "show the sub-district names, and ask 'Which sub-district are you from?'; then call "
                "lookup_khc_reference(reference_type=\"villages\", parent_id=<that sub-district's id>, name=\"\") and show "
                "the village names to pick from. (3) If the village is not listed, ask which listed village is "
                "nearest; use it only if the farmer clearly says yes, and write their real village name in the "
                "complaint text."
            ),
        }.get(needs, "Ask the farmer for the place name again.")
        return f"No place named '{place}' was found. {ask}"
    lines = []
    for index, match in enumerate(matches, 1):
        level = match.get("level", "place").replace("-", " ")
        chain = _chain(match)
        lines.append(f"{index}. {level} {match['name']}" + (f" - {chain}" if chain else "") + f" [{_ids(match)}]")
    listing = "\n".join(lines)
    if len(matches) == 1:
        match = matches[0]
        if match.get("level") == "village":
            return (
                f"Found one place:\n{listing}\nUse it for the grievance location and do not ask the farmer "
                "for state, district, sub-district or village again. Keep the IDs internal."
            )
        return (
            f"Matched a {match.get('level')} only:\n{listing}\nKeep the IDs internal. Still ask "
            "'Which village are you from?' (or the next missing level), then call this tool again with "
            "the village and this district/state."
        )
    # Every row already carries its full chain, so one pick of a "Sub-district, District" option is enough.
    options = "\n".join(
        f"- {m['name']}: {(m.get('subDistrict') or {}).get('name', '?')} sub-district, "
        f"{(m.get('district') or {}).get('name', '?')} district"
        for m in matches
    )
    cut = int(fields.get("total", "0") or 0) > len(matches)
    more = (
        " The list was cut: if the farmer does not see their place, ask 'Which district are you from?' "
        "and call this tool again with that district."
        if cut else ""
    )
    return (
        f"Several places named '{place}' in this state:\n{options}\n(For your use only, the rows with IDs:)\n"
        f"{listing}\nAsk 'Which sub-district are you from?' and show the options above as a list "
        "(sub-district and district names, no IDs) for the farmer to pick by name. When they pick one, the village "
        f"is already known: call this tool again with place='{place}', the same state, and sub_district=<their pick>. "
        f"Do not search for the sub-district name as a place and do not ask for the village again.{more}"
    )


def _render_tree(fields: dict[str, str], scheme_name: str = "", concern_given: bool = False) -> str:
    needs = fields.get("needs", "").strip('"')
    if needs == "state":
        names = "\n".join(f"- {m['name']}" for m in json.loads(fields.get("matches", "[]")))
        return f"Ask 'Which state are you from?' and show these names:\n{names}"
    state = json.loads(fields.get("state", "null"))
    if not isinstance(state, dict):
        raise ValueError("Missing state")
    if needs == "scheme":
        schemes = json.loads(fields.get("schemes", "[]"))
        lines = "\n".join(f"- {s['name']} [schemeId={s['id']}, divisionId={s['division']}]" for s in schemes)
        if fields.get("match", "").strip('"') == "none":
            return (
                f"State: {state['name']} [stateId={state['id']}]. '{scheme_name}' is not a scheme KHC takes "
                "complaints for in this state. Say so in simple words, show only these scheme names "
                f"(no IDs) and ask if the complaint is about one of them:\n{lines}\n"
                "If it is not, tell the farmer you cannot register this complaint here and that they can "
                "contact that scheme's own helpline or the nearest agriculture office. Do not pick a scheme for them."
            )
        return (
            f"State: {state['name']} [stateId={state['id']}]. No single scheme matched. "
            f"Show only the scheme names (no IDs) and ask which one:\n{lines}"
        )
    scheme = json.loads(fields.get("scheme", "null"))
    categories = json.loads(fields.get("categories", "[]"))
    suggested = json.loads(fields.get("suggested", "null"))
    lines = [
        f"State: {state['name']} [stateId={state['id']}]",
        f"Scheme: {scheme['name']} [schemeId={scheme['id']}, divisionId={scheme['division']}]",
    ]
    if isinstance(suggested, dict):
        category, sub = suggested["category"], suggested["subCategory"]
        lines.append(
            f"Best match for the complaint: category '{category['name']}' [problemCategoryId={category['id']}], "
            f"sub-category '{sub['name']}' [problemSubCategoryId={sub['id']}]. Use it and show both names in the "
            "summary for the farmer to confirm or change. If the farmer wants to change it, show the "
            "sub-category names of that category (no IDs) and let them pick by name."
        )
    elif concern_given:
        lines.append(
            "No clear match for the complaint. Do not guess. Show the category names (no IDs) and ask "
            "'Which one is closest to your problem?'. After they pick, show that category's sub-category "
            "names and ask the same. Then put both names in the summary."
        )
    else:
        lines.append(
            "The complaint is not known yet. Ask for it, then call this tool again with it as concern "
            "so the best match can be found."
        )
    lines.append("All categories and sub-categories (IDs are for your use only):")
    for category in categories:
        subs = "; ".join(f"{sub['name']} [{sub['id']}]" for sub in category["subCategories"])
        lines.append(f"- {category['name']} [problemCategoryId={category['id']}]: {subs or 'none listed'}")
    return "\n".join(lines)


async def _call(ctx: RunContext[FarmerContext], action: str, **values: str) -> str:
    failure = _UNCONFIRMED if (
        action == "init" and values.get("request_type", "create_grievance") == "create_grievance"
    ) else "KHC service is unavailable. Please try again later."
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
        return "KHC service is not configured. Please try again later."

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
        expected = "grievance_status" if action == "status" else {
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
                mobile_hint = values.get("farmerMobileNumber")
                return (
                    "KHC asked to re-verify mobile before submission. Send a fresh OTP to the same mobile number"
                    + (f" ({mobile_hint})" if mobile_hint else "")
                    + ", then verify it with verify_khc_otp before retrying submission. "
                    "Do not ask the farmer for the mobile number again if it is already captured."
                )
            if code in (
                "not_found", "invalid_request", "submission_unconfirmed", "upstream_error",
                "configuration_error", "otp_required", "invalid_otp", "rate_limited",
            ):
                return message or failure
            raise ValueError("Missing KHC outcome tag")
        if code == "otp_sent":
            return (
                "OTP sent successfully. Ask the farmer for the OTP received on their mobile. "
                "When they supply six digits, call verify_khc_otp with the same mobile and the supplied OTP. "
                "Do not reject a six-digit reply without a verification tool result."
            )
        if code == "otp_verified":
            if fields.get("isRegistered") not in ("true", "false"):
                raise ValueError("Missing KHC registration state")
            registered = fields["isRegistered"] == "true"
            return (
                "Mobile verified successfully. Farmer is "
                + ("already registered" if registered else "not yet registered")
                + " with KHC. This is the last step: the scheme, location, complaint and sub-category were "
                "already confirmed, so do not ask for them again. "
                + (
                    ""
                    if registered else
                    "Ask only for the farmer's name and gender (Male, Female or Other), then register. "
                )
                + "Call register_khc_grievance now with the confirmed details and the IDs already found."
            )
        if code == "khc_location":
            return f"{_render_location(fields, values.get('name', ''))}\n{SOURCE}"
        if code == "khc_grievance_tree":
            return f"{_render_tree(fields, values.get('scheme_name', ''), bool(values.get('concern')))}\n{SOURCE}"
        if code == "khc_reference":
            options = json.loads(fields.get("options", "null"))
            if not isinstance(options, list) or not all(isinstance(option, dict) for option in options):
                raise ValueError("Invalid reference options")
            return (
                f"KHC {values.get('reference_type')} options: {json.dumps(options, ensure_ascii=False)}\n"
                f"Total matches: {fields.get('total', '')}; page: {fields.get('page', '')} (50 per page).\n"
                "If the farmer-provided scheme/location/category value does not match a returned row, "
                "show the relevant returned names for selection by name and offer next pages when available. "
                "Match the farmer's problem to the returned scheme/category/subcategory names; "
                "reuse a scheme already stated and ask for scheme only when still missing; never ask for internal IDs/codes. "
                "Take scheme and division from the same row, "
                "then fetch categories using scheme and sub-categories using problemcategory. "
                "Use only returned IDs in the payload. Ask a focused clarification only when the problem "
                "does not distinguish the matching options. If a keyword filter returns no match, "
                "fetch the unfiltered list and remaining pages before concluding there is no match. "
                "Treat missing spaces and case differences as name variations, not invalid input: "
                "uttarpradesh matches Uttar Pradesh. Never ask for a proper name just to fix spacing. "
                "If still unmatched, review unfiltered states before asking a focused clarification; "
                "resolve the state before requesting its district. "
                "AIF, A.I.F. and Agriculture Infrastructure Fund refer to the same scheme; "
                "match the named scheme to the returned row in this state's list. "
                "Do not ask the farmer to reconfirm AIF when already stated. "
                "A failed lookup is not evidence that a scheme is unsupported; explain the lookup failure. "
                "For sub-categories, review the unfiltered list under the verified category and its remaining "
                "pages, matching the meaning of the complaint rather than requiring identical wording. "
                "Pick the single best-fit category/sub-category automatically and proceed with that mapping. "
                "Ask the farmer to choose from the returned sub-category names only when no supported fit "
                "can be determined automatically. If none fit, explain that registration is blocked; "
                "do not invent an ID or choose an unrelated option. "
                "Do not offer to proceed with only a category. "
                "Never claim readiness or ask for final submission confirmation until all required IDs are resolved. "
                "For location lists, match the supplied/geocoded place within the returned hierarchy "
                "and ask only when missing or ambiguous. If the district is unknown, ask for it first; "
                "never guess it from a village name. Once the district is verified, fetch sub-districts "
                "with its districtId and name=\"\". If the sub-district is missing, show a bulleted list "
                "of returned sub-district names (sub-districts/tehsils) for the farmer to select by name only, "
                "rather than asking them to know the name. Offer remaining pages when available. "
                "Never invent options or present an empty list as a choice; explain lookup failures. "
                "After selection, fetch villages under the selected subdistrictid and match the village "
                "already supplied, asking only if ambiguous or absent. "
                "If only a village/place name is provided, use forward_geocode and reverse_geocode to "
                "derive missing district/state fields before asking the farmer again. "
                f"Resolve the village ID before asking for final submission confirmation.\n{SOURCE}"
            )
        if not fields.get("grievanceNo") or (action == "status" and (
            not fields.get("currentStatus") or fields["grievanceNo"] != values.get("grievanceNo")
        )):
            raise ValueError("Missing or mismatched KHC grievance details")
        lines = [message, f"Grievance number: {fields['grievanceNo']}"]
        for field, label in (("currentStatus", "Status"), ("currentLevel", "Current level"), ("createdAt", "Registered on")):
            if fields.get(field):
                lines.append(f"{label}: {fields[field]}")
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
        return "Ask the farmer for their 10-digit mobile number."
    if not ctx.deps.session_id:
        return "KHC registration requires an active conversation."
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
    Collect and confirm grievance details as per flow; use this result before final submission.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return "Ask the farmer for their 10-digit mobile number."
    code = to_ascii_digits(str(otp or "")).strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        return "Ask the farmer for the 6-digit OTP received on their mobile."
    if not ctx.deps.session_id:
        return "KHC registration requires an active conversation."
    return await _call(ctx, "init", request_type="verify_otp", farmerMobileNumber=mobile, otp=code)


@observe(name="tool:find_khc_location", as_type="tool")
async def find_khc_location(
    ctx: RunContext[FarmerContext], place: str, state: str = "", district: str = "",
    sub_district: str = "",
) -> str:
    """Find where a place is: its state, district, sub-district and village IDs, in one call.

    Use this first for the KHC grievance location, with the village or place name the
    farmer gave (ask for the village first). Pass state and/or district when known. When the farmer has
    picked a sub-district from a list, call again with the same village as place and that sub-district
    as sub_district; it then returns exactly one place. Names
    may have spacing, case or small spelling differences. A place that is a district also
    matches. If nothing matches, the result says what to ask next. Do not use for status.
    """
    place, state, district = place.strip(), state.strip(), district.strip()
    sub_district = sub_district.strip()
    if not place or len(place) > 200 or max(len(state), len(district), len(sub_district)) > 100:
        return "Ask the farmer for their village or place name (up to 200 characters)."
    return await _call(
        ctx, "init", request_type="find_location", name=place, state_name=state, district_name=district,
        sub_district_name=sub_district,
    )


@observe(name="tool:get_khc_grievance_options", as_type="tool")
async def get_khc_grievance_options(
    ctx: RunContext[FarmerContext], state: str, scheme: str = "", concern: str = "",
) -> str:
    """Get a state's KHC grievance options in one call.

    Without a scheme it lists the state's schemes. With the scheme the farmer named
    (AIF, A.I.F. and the full name all work) it returns that scheme's categories with all
    their sub-categories and the IDs needed to register. Call it again once the complaint is
    known and pass concern: a short English summary of the complaint (up to 500 characters).
    The result then suggests the best-fit category and sub-category when one clearly matches.
    Prefer this over three separate lookup_khc_reference calls.
    """
    state, scheme, concern = state.strip(), scheme.strip(), " ".join(concern.split())
    if not state or len(state) > 100 or len(scheme) > 100 or len(concern) > 500:
        return "Ask the farmer which state they are from."
    return await _call(
        ctx, "init", request_type="grievance_tree", state_name=state, scheme_name=scheme,
        concern=concern,
    )


@observe(name="tool:lookup_khc_reference", as_type="tool")
async def lookup_khc_reference(
    ctx: RunContext[FarmerContext], reference_type: str,
    parent_id: Optional[int] = None, name: str = "", page: int = 1,
) -> str:
    """Look up official KHC IDs by name; never guess IDs.

    Lists and parents: states (none), districts (stateId), sub-districts (districtId),
    villages (subDistrictId), schemes (stateId), categories (scheme),
    sub-categories (problemCategory). Choose the scheme and division from the SAME
    returned row. Follow the farmer's location through each level to the village.
    For schemes, fetch options for the resolved state and match the farmer's problem
    to schemename; do not require the farmer to know the scheme name or any IDs.
    Then match categoryname under the selected scheme and subcategoryname under the
    selected category. Use a focused clarification when several schemes fit; a generic
    loan complaint alone does not establish AIF. Reuse a scheme already stated.
    AIF, A.I.F. and Agriculture Infrastructure Fund are equivalent names; verify
    against the state's returned schemes without asking the farmer to repeat AIF.
    Match sub-categories by meaning, not exact complaint wording. If ambiguous,
    show relevant API-returned names for selection by name or ask a focused question.
    If no match exists after unfiltered lookup and pagination, explain the blocker.
    Do not offer category-only submission or final confirmation with unresolved IDs.
    Reuse an explicitly stated grievance location first. Otherwise use reverse_geocode
    on available Browser Location Coordinates, then look up the returned place names.
    Only ask the farmer for location details not already resolved; never guess a village.
    If the district cannot be established from the village details, ask for the district;
    never infer it from the village name alone. Verify the district in the state's list.
    When the sub-district is missing, fetch sub-districts for that district with name=""
    and show the returned names as bulleted choices, with further pages if available.
    Ask the farmer to select by name only, then fetch villages under the selected subdistrictid and
    match the village already supplied. Do not ask the farmer for internal IDs.
    Resolve the village ID before requesting final submission confirmation.
    name is a case/whitespace-insensitive literal substring filter on reference names,
    NOT a semantic problem search. uttarpradesh matches Uttar Pradesh; do not ask
    the farmer to reformat a recognizable name. Resolve state before district.
    Do not pass the entire complaint as name. Use name="" to review options when no
    reliable name keyword is known or a filter finds nothing. Review further pages
    before concluding no suitable option exists (50 options/page).
    """
    parents = {
        "states": None, "districts": "stateId", "sub-districts": "districtId",
        "villages": "subDistrictId", "schemes": "stateId", "categories": "scheme",
        "sub-categories": "problemCategory",
    }
    if reference_type not in parents or type(page) is not int or page <= 0:
        return "Choose a valid KHC reference list and positive page number."
    if parents[reference_type] and (type(parent_id) is not int or not 0 < parent_id <= 9007199254740991):
        return f"Look up and confirm the parent {parents[reference_type]} before requesting {reference_type}."
    if len(name) > 200:
        return "Use a location, scheme or category name of up to 200 characters."
    values = {"request_type": "get_reference", "reference_type": reference_type, "name": name, "page": str(page)}
    if parent_id is not None:
        values["parent_id"] = str(parent_id)
    return await _call(ctx, "init", **values)


@observe(name="tool:check_khc_grievance_status", as_type="tool")
async def check_khc_grievance_status(
    ctx: RunContext[FarmerContext], grievance_number: str = "",
) -> str:
    """Check KHC grievance status. Ask the farmer for their grievance number if missing.

    Keep slashes and leading zeros intact, e.g. BR/240926/2. Do not ask for an OTP,
    mobile, beneficiary ID or cookie. Never invent a grievance number.
    """
    number = to_ascii_digits(str(grievance_number or "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/-]{0,99}", number):
        return "Ask the farmer for their Kisan Help Center grievance number, including any slashes."
    return await _call(ctx, "status", grievanceNo=number)


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
    New farmers also need name and
    gender code. All six numeric IDs MUST come from verified KHC master data, not LGD
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
        return "Ask the farmer for their 10-digit mobile number."
    if farmer_type not in ("new", "existing"):
        return "Send and verify the farmer's mobile OTP first, then use the registration state returned by KHC."
    if not grievance_text.strip() or len(grievance_text) > 5000:
        return "Ask the farmer to describe their grievance in up to 5000 characters."
    farmer_language = farmer_language.strip().lower()
    farmer_language = _LANGUAGE_CODES.get(farmer_language, farmer_language)
    if farmer_language not in _LANGUAGE_CODES.values():
        return (
            "Ask the farmer for a supported KHC language from the OAN UI: Hindi, English, "
            "Marathi, Bengali, Gujarati, Kannada, Tamil, Telugu, Assamese or Malayalam. "
            "Maithili is available in the UI but is not currently accepted by the KHC provider; "
            "ask the farmer to choose a supported language without silently substituting one."
        )
    gender = gender.strip().upper()
    gender = {"MALE": "M", "FEMALE": "F", "OTHER": "O"}.get(gender, gender)
    if farmer_type == "new" and (not farmer_name.strip() or gender not in ("M", "F", "O")):
        return "Ask the new farmer for their name and gender before registering."
    ids = (state_id, village_id, scheme_id, division_id, problem_category_id, problem_subcategory_id)
    if any(type(value) is not int or not 0 < value <= 9007199254740991 for value in ids):
        return (
            "Verified KHC state, village, scheme, division, problem category and subcategory "
            "mappings are required. Call lookup_khc_reference to resolve them from the farmer's "
            "location and problem description: state -> schemes (take scheme and division from the same row) "
            "-> categories -> sub-categories, and state -> districts -> sub-districts -> villages. "
            "If the district is unknown, ask for it; once verified, fetch its sub-districts with name=\"\" "
            "and show the returned names as bulleted choices for selection by name only when the sub-district is missing. "
            "Fetch villages under the selected sub-district and match the village already supplied. "
            "Resolve all mappings before requesting final submission confirmation. "
            "Match returned names to the problem; ask a focused clarification only if ambiguous. "
            "Do not guess IDs. Reuse scheme already provided and ask for scheme only when missing. "
            "Never ask the farmer for internal IDs/codes."
        )
    if not confirmed:
        return "Summarize the farmer's grievance and registration details, then ask for explicit confirmation to submit."
    if not ctx.deps.session_id:
        logger.error("khc.registration_missing_session")
        return "KHC registration requires an active conversation. Please start a new conversation."
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
