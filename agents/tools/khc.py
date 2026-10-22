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
SOURCE = "**Source:** Kisan Help Center"
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
        }.get(values.get("request_type"), "grievance_created")
        if code != expected:
            if code in (
                "not_found", "invalid_request", "submission_unconfirmed", "upstream_error",
                "configuration_error", "otp_required", "invalid_otp",
            ):
                return message or failure
            raise ValueError("Missing KHC outcome tag")
        if code == "otp_sent":
            return (
                "OTP sent successfully. Ask the farmer for the OTP received on their mobile. "
                "When they supply six digits, call verify_khc_otp with the same mobile and the supplied OTP. "
                "Do not reject a six-digit reply without a verification tool result. "
                "Do not ask for grievance language or other remaining details until verification succeeds."
            )
        if code == "otp_verified":
            if fields.get("isRegistered") not in ("true", "false"):
                raise ValueError("Missing KHC registration state")
            return (
                "Mobile verified successfully. Farmer is "
                + ("already registered" if fields["isRegistered"] == "true" else "not yet registered")
                + " with KHC. Reuse details already supplied. Ask for the problem in their own words "
                "only if it is not already known. Do not ask for a scheme name or any internal IDs upfront. "
                "Resolve the grievance location from permitted browser coordinates or supplied place names; "
                "ask only for missing location details. Use lookup_khc_reference to fetch schemes for the "
                "resolved state, match the problem to the returned scheme names, then fetch categories and "
                "sub-categories. Ask a focused clarification if the problem does not identify a unique match. "
                "For a new farmer collect missing name and gender; obtain confirmation before submission."
            )
        if code == "khc_reference":
            options = json.loads(fields.get("options", "null"))
            if not isinstance(options, list) or not all(isinstance(option, dict) for option in options):
                raise ValueError("Invalid reference options")
            return (
                f"KHC {values.get('reference_type')} options: {json.dumps(options, ensure_ascii=False)}\n"
                f"Total matches: {fields.get('total', '')}; page: {fields.get('page', '')} (50 per page).\n"
                "Match the farmer's problem to the returned scheme/category/subcategory names; "
                "do not ask for a scheme name or internal IDs upfront. Take scheme and division from the same row, "
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
                "If several options fit, show the relevant returned sub-category names for selection by name "
                "or ask a focused question that distinguishes them. If none fit, explain that registration "
                "is blocked; do not invent an ID or choose an unrelated option. "
                "Do not offer to proceed with only a category. "
                "Never claim readiness or ask for final submission confirmation until all required IDs are resolved. "
                "For location lists, match the supplied/geocoded place within the returned hierarchy "
                "and ask only when missing or ambiguous. If the district is unknown, ask for it first; "
                "never guess it from a village name. Once the district is verified, fetch sub-districts "
                "with its districtId and name=\"\". If the sub-district is missing, show a bulleted list "
                "of returned sub-district names (tehsils/blocks) for the farmer to select by name only, "
                "rather than asking them to know the name. Offer remaining pages when available. "
                "Never invent options or present an empty list as a choice; explain lookup failures. "
                "After selection, fetch villages under the selected subdistrictid and match the village "
                "already supplied, asking only if ambiguous or absent. "
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
    Do not collect language or other grievance details until verification succeeds.
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
    the problem using the reference APIs; do not ask for scheme names or IDs upfront.
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
            "Do not guess IDs or ask the farmer for a scheme name or internal codes upfront."
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
