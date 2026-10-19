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
            return "OTP sent successfully. Ask the farmer for the OTP received on their mobile."
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
                "For location lists, match the supplied/geocoded place within the returned hierarchy "
                f"and ask only when missing or ambiguous.\n{SOURCE}"
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
    Reuse an explicitly stated grievance location first. Otherwise use reverse_geocode
    on available Browser Location Coordinates, then look up the returned place names.
    Only ask the farmer for location details not already resolved; never guess a village.
    name is a literal substring filter on reference names, NOT a semantic problem search.
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
    if farmer_language not in ("hi", "en", "mr", "bn", "gu", "kn", "ta", "te", "pa", "or", "as", "ml", "ur", "ne", "mni"):
        return "Ask the farmer for a supported KHC language: Hindi, English, Marathi, Bengali, Gujarati, Kannada, Tamil, Telugu, Punjabi, Odia, Assamese, Malayalam, Urdu, Nepali or Manipuri."
    if farmer_type == "new" and (not farmer_name.strip() or gender not in ("M", "F", "O")):
        return "Ask the new farmer for their name and gender before registering."
    ids = (state_id, village_id, scheme_id, division_id, problem_category_id, problem_subcategory_id)
    if any(type(value) is not int or not 0 < value <= 9007199254740991 for value in ids):
        return (
            "Verified KHC state, village, scheme, division, problem category and subcategory "
            "mappings are required. Call lookup_khc_reference to resolve them from the farmer's "
            "location and problem description: state -> schemes (take scheme and division from the same row) "
            "-> categories -> sub-categories, and state -> districts -> sub-districts -> villages. "
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
