"""
PM-KISAN grievance tools over Beckn v2.

Same three tools, same arguments, same purpose as agents.tools.pmkisan_grievance
-- the transport changes, and two things about the shape of the conversation
change with it:

  * One round-trip disappears. v1 asked for an OTP, verified it in a call of its
    own, then made a third call to do the work. v2 carries the OTP inside the
    working call as challenge.response, and the provider adapter verifies it
    before it fetches, so "wrong OTP" and "no such registration" are both one
    refusal on one call.

  * Status reads ONE grievance, not all of them. v1's search returned every
    record held against the registration; v2's PMKISANStatus requires
    case.filedOn and the mapping matches a single record on it. So the v2 tool
    takes a filing date the v1 tool never needed -- see pmkisan_grievance_status.

Two gaps are known and deliberate rather than overlooked, both in
pmkisan_submit_grievance; they are documented at _CATEGORY_CODES below and in
migration-plan/pmkisan/plan.md.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.pmkisan_grievance import (
    GRIEVANCE_TYPES,
    GrievanceTypeLiteral,
    _PMKISAN_GRIEVANCE_SUBMIT_DISABLED_MSG,
    _validate_pmkisan_registration_number,
    generate_transaction_id,
)
from agents.tools.v2.pmkisan_scheme_status import (
    ACTIVE,
    DRAFT,
    GRIEVANCE_CONTEXT,
    GRIEVANCE_TYPE,
    SCHEME,
    _first_resource,
    adapter_error,
    build_context,
    clean_otp,
    commitment_attributes,
    commitment_envelope,
    explain_failure,
    post_action,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

# discover returns no offers, so this stays a constant; the provider and the
# resource id come off the wire.
_GRIEVANCE_OFFER = "off:pmkisan:grievance"

_UNAVAILABLE_MSG = (
    "The PM-KISAN grievance desk is not available right now. "
    "Please try again later."
)


async def _discover_grievance() -> Optional[Dict[str, Any]]:
    """The PM-KISAN grievance resource, with its catalog provider.

    The scheme code is not a nicety here: PMFBY publishes openagrinet:Grievance
    too, so matching on @type alone reaches the wrong desk.
    """
    body = {
        "context": build_context("discover", str(uuid.uuid4())),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        f'@.resourceAttributes."@type" == "{GRIEVANCE_TYPE}" && '
                        f'@.resourceAttributes.scheme.code == "{SCHEME["code"]}")'
                    ),
                }
            }
        },
    }
    response = await post_action("discover", body, who="PM-KISAN")
    if response.status_code != 200:
        logger.error(
            f"PM-KISAN v2 grievance discover failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
        return None
    return _first_resource(response.json())

# The v0.1 pack enumerates these; anything else is SCH_INVALID_ENUM.
_PUBLISHED_CATEGORIES = {
    "G001": "Account number not correct",
    "G002": "Online application pending for approval",
    "G003": "Installment not received",
    "G004": "Transaction failed",
    "G005": "Problem in Aadhaar correction",
    "G006": "Gender not correct",
    "G007": "Payment related",
    "G008": "Problem in OTP-based eKYC",
    "G009": "Problem in biometric-based eKYC",
    "G010": "Problem in facial-based eKYC",
}

# The seven of v1's twenty labels with an equivalent heading above. The other
# thirteen are refused rather than filed under a near-enough one; why, and what
# it would take to close the gap, is in migration-plan/pmkisan/plan.md.
_CATEGORY_CODES = {
    "LATEST_INSTALMENT_NOT_RECEIVED": "G003",
    "PREVIOUS_INSTALMENTS_NOT_RECEIVED": "G003",
    "TRANSACTION_FAILED": "G004",
    "PROBLEM_WITH_BIOMETRIC_EKYC": "G009",
    "PROBLEM_WITH_FACIAL_EKYC": "G010",
    "PENDING_APPROVAL_FOR_SELF_REGISTRATION": "G002",
    "PROBLEM_WITH_SEEDING_AADHAAR_IN_BANK_ACCOUNT": "G001",
}

def category_for(grievance_type: str) -> Tuple[str, str]:
    """(category code, refusal to read to the farmer). Exactly one is non-empty.

    Module level rather than inline in pmkisan_submit_grievance, because that
    function returns the disabled message on its first line and everything below
    it is unreachable. This gate would otherwise ship having never run.
    """
    code = _CATEGORY_CODES.get(grievance_type)
    if code:
        return code, ""
    headings = "\n".join(f"- {name}" for name in _PUBLISHED_CATEGORIES.values())
    return "", (
        "PM-KISAN does not currently accept this kind of grievance through "
        "this service. It accepts complaints under these headings only:\n"
        f"{headings}\n"
        "For anything else, please use the PM-KISAN helpline or portal."
    )


_OTP_SENT_MSG = "An OTP has been sent to the mobile number registered with PM-KISAN."

_NO_FILING_DATE_MSG = (
    "To look up a PM-KISAN grievance I need the date it was filed, because the "
    "scheme identifies a complaint by its filing date. Please share that date "
    "(for example 28-09-2026). It was shown when the grievance was submitted."
)


def _grievance_attributes(reg_no: str) -> Dict[str, Any]:
    return {
        "@context": GRIEVANCE_CONTEXT,
        "@type": GRIEVANCE_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "enrolmentId": reg_no,
    }


def _iso_date(value: str) -> str:
    """A farmer's filing date as the ISO calendar date the schema requires.

    DD-MM-YYYY is what the rest of this codebase passes around and what a farmer
    reads off a receipt; DD/MM/YYYY and an already-ISO date are accepted too.
    Anything else returns "" so the caller can ask again rather than send a
    date the validator will bounce.
    """
    text = to_ascii_digits(value or "").strip()
    if not text:
        return ""
    # Every branch goes through strptime, including the already-ISO one: a date
    # like 2026-13-45 is the right shape and no day at all, and sending it would
    # come back as "no grievance filed on that date" -- a wrong answer, not an
    # error the farmer can act on.
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _display_date(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return iso or ""


def _format_case(attributes: Dict[str, Any]) -> str:
    """The grievance as lines a farmer can be read."""
    case = attributes.get("case") or {}
    grievance = attributes.get("grievance") or {}
    lines = []

    if attributes.get("enrolmentId"):
        lines.append(f"Registration Number: {attributes['enrolmentId']}")
    if case.get("ticketNo"):
        lines.append(f"Grievance ID: {case['ticketNo']}")
    if case.get("filedOn"):
        lines.append(f"Grievance Date: {_display_date(case['filedOn'])}")
    if (case.get("status") or {}).get("code"):
        lines.append(f"Grievance Status: {case['status']['code']}")
    if grievance.get("description"):
        lines.append(f"Grievance Description: {grievance['description']}")
    if case.get("reply"):
        lines.append(f"Officer Reply: {case['reply']}")
    if case.get("repliedOn"):
        lines.append(f"Office Reply Date: {_display_date(case['repliedOn'])}")

    if not lines:
        return "PM-KISAN returned no grievance details. Please try again later."
    return "\n".join(lines)


async def _send_grievance_otp(ctx: RunContext[FarmerContext], reg_no: str, purpose: str) -> str:
    transaction_id = generate_transaction_id(ctx.deps.session_id, reg_no)
    lf_update_current_observation(
        metadata={
            "tool": "pmkisan_grievance.send_otp",
            "transaction_id": transaction_id,
            "beckn_version": "v2",
        }
    )
    found = await _discover_grievance()
    if not found:
        logger.warning("PM-KISAN v2 discover found no grievance catalog")
        return _UNAVAILABLE_MSG
    body = commitment_envelope(
        "init",
        transaction_id,
        DRAFT,
        _grievance_attributes(reg_no),
        _GRIEVANCE_OFFER,
        found,
    )
    response = await post_action("init", body, who="PM-KISAN")
    if response.status_code != 200:
        return explain_failure(response, "OTP request")

    issued = (commitment_attributes(response).get("challenge") or {}).get("issued") or {}
    length = issued.get("length") or 4
    return f"{_OTP_SENT_MSG}\nPlease provide the {length}-digit OTP to {purpose}."


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:pmkisan_grievance_send_otp", as_type="tool")
async def pmkisan_grievance_send_otp(
    ctx: RunContext[FarmerContext],
    reg_no: str,
    phone_number: str = "",
    purpose: Literal["submit_grievance", "check_status"] = "submit_grievance",
) -> str:
    """
    Send OTP to the mobile registered with a PM-KISAN registration number.

    Use before submitting a grievance or checking grievance status. After the user shares
    the OTP, call pmkisan_submit_grievance or pmkisan_grievance_status with that OTP.

    Args:
        reg_no: PM-KISAN registration number used for OTP.
        phone_number: Not used on v2 and not worth asking the farmer for. The
            Grievance capability has no field for a mobile number -- sending one
            is SCH_FIELD_NOT_ALLOWED -- and PM-KISAN texts the number already
            registered against reg_no. Kept only so the v1 and v2 signatures
            match.
        purpose: Whether the OTP is for grievance submission or status check.

    Returns:
        OTP send confirmation or service error.
    """
    # ponytail: Remove this block to re-enable submit grievance OTP flow.
    # Carried over from v1 unchanged; lifting it here would enable lodging on v2 only.
    if purpose == "submit_grievance":
        return _PMKISAN_GRIEVANCE_SUBMIT_DISABLED_MSG

    try:
        reg_no_clean = _validate_pmkisan_registration_number(reg_no)
        purpose_text = (
            "submit the grievance" if purpose == "submit_grievance"
            else "check grievance status"
        )
        return await _send_grievance_otp(ctx, reg_no_clean, purpose_text)

    except httpx.TimeoutException:
        logger.error("PM-KISAN v2 grievance OTP request timed out.")
        return "OTP request timed out. Please try again."
    except httpx.RequestError as e:
        logger.error(f"PM-KISAN v2 grievance OTP network error: {e}")
        return "Unable to reach OTP service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Unexpected error in pmkisan_grievance_send_otp (v2): {e}")
        raise ModelRetry(f"Unexpected error while sending OTP. {str(e)}")


@observe(name="tool:pmkisan_submit_grievance", as_type="tool")
async def pmkisan_submit_grievance(
    ctx: RunContext[FarmerContext],
    reg_no: str,
    grievance_description: str,
    grievance_type: GrievanceTypeLiteral,
    otp: Optional[str] = None,
    phone_number: str = "",
    raw: bool = False,
) -> str:
    """
    Submit a PM-KISAN grievance only after OTP verification.

    Call pmkisan_grievance_send_otp first. Use this tool only after the user shares
    the OTP; it verifies the OTP before calling the grievance submit API.

    Args:
        reg_no: PM-KISAN registration number used for OTP verification and grievance submission.
        grievance_description: Detailed description of the grievance.
        grievance_type: Must be exactly one of: "LATEST_INSTALMENT_NOT_RECEIVED",
            "TRANSACTION_FAILED", "PREVIOUS_INSTALMENTS_NOT_RECEIVED",
            "INCORRECTLY_MARKED_AS_DECEASED", "INCORRECTLY_MARKED_AS_DECEASED_PREVIOUS_LANDOWNER",
            "INCORRECTLY_MARKED_AS_INELIGIBLE", "VOLUNTARY_SURRENDER_BY_MISTAKE",
            "INCORRECTLY_MARKED_UNDER_SUSPECTED_CASES", "PROBLEM_WITH_UPDATING_LAND_DETAILS",
            "PROBLEM_WITH_SEEDING_AADHAAR_IN_BANK_ACCOUNT", "PROBLEM_WITH_BIOMETRIC_EKYC",
            "PROBLEM_WITH_FACIAL_EKYC", "PENDING_APPROVAL_FOR_SELF_REGISTRATION",
            "PENDING_APPROVAL_FOR_INELIGIBILITY_REVOCATION",
            "PENDING_APPROVAL_FOR_SURRENDER_REVOCATION",
            "PENDING_APPROVAL_FOR_UPDATION_OF_MISSING_INFORMATION",
            "PENDING_ACTION_SUSPECTED_SPOUSE_OR_MINOR",
            "PENDING_ACTION_SUSPECTED_INCORRECT_LAND_OR_PREVIOUS_OWNER", "CORRUPTION",
            "URGENT". Map the farmer's free-text complaint to the closest matching value
            from this list; never invent a new label.
        otp: 4-digit OTP received on the registered mobile.
        phone_number: Not used on v2. See pmkisan_grievance_send_otp.
        raw: If True, return the raw response body.

    Returns:
        Grievance submission result or OTP verification error.
    """
    # ponytail: Remove this line to re-enable PM-KISAN grievance submission API call.
    return _PMKISAN_GRIEVANCE_SUBMIT_DISABLED_MSG

    try:
        reg_no_clean = _validate_pmkisan_registration_number(reg_no)

        if grievance_type not in GRIEVANCE_TYPES:
            choices = '", "'.join(GRIEVANCE_TYPES)
            raise ModelRetry(
                f'Invalid grievance type: "{grievance_type}". Please select from: "{choices}".'
            )
        category_code, refusal = category_for(grievance_type)
        if refusal:
            return refusal

        if not grievance_description or len(grievance_description.strip()) < 10:
            raise ModelRetry(
                "Please provide a brief grievance description (at least 10 characters)."
            )

        otp_clean = clean_otp(otp or "")
        if not otp_clean:
            raise ModelRetry(
                "OTP verification is required before submitting a grievance. "
                "First call pmkisan_grievance_send_otp, then call this tool with "
                "the received OTP."
            )

        transaction_id = generate_transaction_id(ctx.deps.session_id, reg_no_clean)
        lf_update_current_observation(
            metadata={
                "tool": "pmkisan_grievance.submit",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_grievance()
        if not found:
            logger.warning("PM-KISAN v2 discover found no grievance catalog")
            return _UNAVAILABLE_MSG
        # The one PM-KISAN action carrying its payload on channels, not a contract.
        body = {
            "context": build_context("support", transaction_id),
            "message": {
                "support": {
                    "channels": [
                        {
                            **_grievance_attributes(reg_no_clean),
                            "provider": found["provider"],
                            "grievance": {
                                "category": {"code": category_code},
                                "description": grievance_description.strip(),
                            },
                            "challenge": {
                                "response": {"method": "OTP", "value": otp_clean}
                            },
                        }
                    ]
                }
            },
        }
        response = await post_action("support", body, who="PM-KISAN")
        if response.status_code != 200:
            return explain_failure(response, "grievance submission")

        if raw:
            return response.text

        try:
            channels = (
                response.json().get("message", {}).get("support", {}).get("channels") or []
            )
        except ValueError:
            channels = []
        if not channels:
            return "PM-KISAN accepted the grievance but returned no reference. Please try again later."

        channel = channels[0]
        case = channel.get("case") or {}
        lines = ["Your PM-KISAN grievance has been registered."]
        if case.get("ticketNo"):
            lines.append(f"Grievance ID: {case['ticketNo']}")
        if case.get("filedOn"):
            lines.append(f"Grievance Date: {_display_date(case['filedOn'])}")
            # The date is how the scheme finds this complaint again.
            lines.append(
                "Please keep this date -- it is needed to check the grievance later."
            )
        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error("PM-KISAN v2 grievance submission timed out.")
        return "Grievance submission timed out. Please try again."
    except httpx.RequestError as e:
        logger.error(f"PM-KISAN v2 grievance submission network error: {e}")
        return "Unable to reach grievance service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Unexpected error in pmkisan_submit_grievance (v2): {e}")
        raise ModelRetry(f"Unexpected error while submitting grievance. {str(e)}")


@observe(name="tool:pmkisan_grievance_status", as_type="tool")
async def pmkisan_grievance_status(
    ctx: RunContext[FarmerContext],
    reg_no: str = "",
    raw: bool = False,
    otp: Optional[str] = None,
    phone_number: str = "",
    filed_on: str = "",
) -> str:
    """
    Check PM-KISAN grievance status only after OTP verification.

    Call pmkisan_grievance_send_otp first with purpose="check_status". Use this tool only
    after the user shares the OTP; it verifies the OTP before calling grievance search.

    Args:
        reg_no: PM-KISAN registration number used for OTP verification and status lookup.
        raw: If True, return raw HTTP body (pretty JSON when possible).
        otp: 4-digit OTP received on the registered mobile.
        phone_number: Not used on v2. See pmkisan_grievance_send_otp.
        filed_on: The date the grievance was filed, as DD-MM-YYYY. PM-KISAN identifies a
            grievance by its filing date, so this is required. Ask the farmer for it; it
            was shown when the grievance was submitted. Do not guess it.

    Returns:
        Grievance status summary or OTP verification error.
    """
    try:
        reg_no_clean = _validate_pmkisan_registration_number(reg_no)

        otp_clean = clean_otp(otp or "")
        if not otp_clean:
            raise ModelRetry(
                "OTP verification is required before checking grievance status. "
                "First call pmkisan_grievance_send_otp with purpose='check_status', "
                "then call this tool with the received OTP."
            )

        filed_on_iso = _iso_date(filed_on)
        if not filed_on_iso:
            return _NO_FILING_DATE_MSG

        transaction_id = generate_transaction_id(ctx.deps.session_id, reg_no_clean)
        lf_update_current_observation(
            metadata={
                "tool": "pmkisan_grievance.status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        attributes = _grievance_attributes(reg_no_clean)
        attributes["case"] = {"filedOn": filed_on_iso}
        attributes["challenge"] = {"response": {"method": "OTP", "value": otp_clean}}
        found = await _discover_grievance()
        if not found:
            logger.warning("PM-KISAN v2 discover found no grievance catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "status",
            transaction_id,
            ACTIVE,
            attributes,
            _GRIEVANCE_OFFER,
            found,
        )
        response = await post_action("status", body, who="PM-KISAN")
        if response.status_code != 200:
            code, _ = adapter_error(response)
            if code == "BIZ_GENERIC_ERROR":
                # Almost always a filing date matching no record, so name it back.
                logger.info(
                    f"No PM-KISAN grievance for {reg_no_clean} filed on {filed_on_iso}"
                )
                return (
                    f"PM-KISAN has no grievance recorded against registration "
                    f"{reg_no_clean} filed on {_display_date(filed_on_iso)}. "
                    "Please check the filing date and try again."
                )
            return explain_failure(response, "grievance status check")

        if raw:
            return response.text
        return _format_case(commitment_attributes(response))

    except httpx.TimeoutException:
        logger.error("PM-KISAN v2 grievance status check timed out.")
        return "Grievance status check timed out. Please try again."
    except httpx.RequestError as e:
        logger.error(f"PM-KISAN v2 grievance status network error: {e}")
        return "Unable to reach grievance status service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Error in pmkisan_grievance_status (v2): {e}")
        raise ModelRetry(f"Unexpected error while checking grievance status. {str(e)}")
