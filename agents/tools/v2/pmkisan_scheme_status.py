"""
PM-KISAN application status over Beckn v2.

Same two tools, same arguments, same purpose as agents.tools.pmkisan_scheme_status
-- only the transport changes. v1 posted /init and /status at the BAP and let the
provider decide what came back; v2 posts the same two actions at the consumer
adapter against the SchemeApplication capability:

    init    -> PM-KISAN sends a four-digit OTP to the registered mobile
    status  -> the application, read with that OTP in challenge.response

One round-trip disappears. v1 verified the OTP in a call of its own and then made
a second call for the data; v2 carries the OTP inside the status request and the
provider adapter verifies it before fetching, so a wrong OTP and a missing
registration both come back as one refusal on one call.

A bad OTP is returned rather than raised. v1 let ModelRetry escape, where the
catch-all re-wrapped it as "Unexpected error ..."; here the message reaches the
farmer as written. This is the third behavioural difference, and the reason the
two versions cannot be diffed blind.

The output is NOT byte-comparable with v1. v1 rendered whatever tag soup the
gateway returned; v2 gets named fields out of the v0.2 schema pack and renders
them here, so the two versions must be read side by side rather than diffed.
See migration-plan/pmkisan/plan.md.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.pmkisan_scheme_status import generate_transaction_id
from agents.tools.v2.aif import (
    _first_resource,
    post_action,
    scheme_application_context,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

SCHEME_APPLICATION_TYPE = "openagrinet:SchemeApplication"
GRIEVANCE_CONTEXT = (
    "https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/context.jsonld"
)
GRIEVANCE_TYPE = "openagrinet:Grievance"

SCHEME = {"code": "PM-KISAN"}

# discover returns catalogs and resources, never offers, so this one stays a
# constant -- the same reason weather.py keeps _OFFER_ID. The provider and the
# resource id come off the wire.
_APPLICATION_OFFER = "off:pmkisan:application-status"

# DRAFT while the OTP is still being asked for, ACTIVE once it is spent.
DRAFT = "DRAFT"
ACTIVE = "ACTIVE"

_OTP_SENT_MSG = (
    "An OTP has been sent to the mobile number registered with PM-KISAN."
)
_UNAVAILABLE_MSG = (
    "PM-KISAN application status is not available right now. "
    "Please try again later."
)
_NO_IDENTIFIER_MSG = (
    "Please provide either a PM Kisan registration number or a registered phone "
    "number to check the status."
)


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------

def adapter_url(action: str) -> str:
    base = os.getenv("OAN_ADAPTER_URL", "http://network-adapter:9201/consumer")
    return base.rstrip("/") + "/" + action


def build_context(action: str, transaction_id: str) -> Dict[str, Any]:
    """The v2 envelope. One network id on all three fields: this is a
    single-network deployment, so sender, receiver and network are the same."""
    network_id = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return {
        "domain": os.getenv("OAN_DOMAIN", "agriculture"),
        "action": action,
        "version": "2.0.0",
        "networkId": network_id,
        "senderId": network_id,
        "receiverId": network_id,
        "transactionId": transaction_id,
        "messageId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }


def commitment_envelope(
    action: str,
    transaction_id: str,
    state: str,
    attributes: Dict[str, Any],
    offer_id: str,
    found: Dict[str, Any],
    contract_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The one contract-with-one-commitment shape every PM-KISAN action uses.

    `found` is what _discover_application returned: the catalog's provider and
    the resource it published.
    """
    resource_id = found["resource"]["id"]
    return {
        "context": build_context(action, transaction_id),
        "message": {
            "contract": {
                "id": contract_id or str(uuid.uuid4()),
                "commitments": [
                    {
                        "status": {"descriptor": {"code": state}},
                        "offer": {
                            "id": offer_id,
                            "provider": found["provider"],
                            "resourceIds": [resource_id],
                        },
                        "resources": [{"id": resource_id, "quantity": {"count": 1}}],
                        "commitmentAttributes": attributes,
                    }
                ],
            }
        },
    }


def adapter_error(response: httpx.Response) -> Tuple[str, str]:
    """(code, message) out of a NACK; empty strings when the body is not one."""
    try:
        error = ((response.json().get("message") or {}).get("error")) or {}
    except ValueError:
        return "", ""
    return str(error.get("code") or ""), str(error.get("message") or "")


def explain_failure(response: httpx.Response, what: str) -> str:
    """What to tell the farmer when the adapter refused.

    A BIZ_ error is PM-KISAN's own answer -- "no registration for this
    applicant", "the OTP is wrong" -- and is written to be read aloud, so it is
    passed through. Anything else is ours or the network's, and saying so in
    detail would only puzzle the farmer.
    """
    code, message = adapter_error(response)
    logger.error(
        f"PM-KISAN v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if code.startswith("BIZ_") and message:
        return message
    if code.startswith("SCH_"):
        return f"PM-KISAN {what} could not be completed. Please try again later."
    return f"PM-KISAN {what} is temporarily unavailable. Please try again later."


async def _discover_application() -> Optional[Dict[str, Any]]:
    """The PM-KISAN application-status resource, with its catalog provider.

    Matching on @type alone would return every scheme publishing
    SchemeApplication, so the scheme code is half of the match.
    """
    body = {
        "context": build_context("discover", str(uuid.uuid4())),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        f'@.resourceAttributes."@type" == "{SCHEME_APPLICATION_TYPE}" && '
                        f'@.resourceAttributes.scheme.code == "{SCHEME["code"]}")'
                    ),
                }
            }
        },
    }
    response = await post_action("discover", body, who="PM-KISAN")
    if response.status_code != 200:
        logger.error(
            f"PM-KISAN v2 discover failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
        return None
    return _first_resource(response.json(), who="PM-KISAN")


def commitment_attributes(response: httpx.Response) -> Dict[str, Any]:
    """The attributes off the first commitment of an on_* response."""
    try:
        commitments = (
            response.json().get("message", {}).get("contract", {}).get("commitments") or []
        )
    except ValueError:
        return {}
    if not commitments:
        return {}
    return commitments[0].get("commitmentAttributes") or {}


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------

def split_identifier(reg_no: str, phone_number: str) -> Tuple[str, str]:
    """(reg_no, phone_number) with the farmer's input routed to the right one.

    Same rule as v1: local-script digits folded to ASCII, spaces dropped, and a
    bare ten digits read as a mobile number whichever argument it arrived in.
    """
    value = to_ascii_digits(reg_no or phone_number).strip().upper().replace(" ", "")
    if not value:
        return "", ""
    if re.fullmatch(r"\d{10}", value):
        return "", value
    return value, ""


def applicant_attributes(reg_no: str, phone_number: str) -> Dict[str, Any]:
    """enrolmentId when we have a registration number, applicant.mobile otherwise.

    The mapping branches on exactly this: enrolmentId present means a Ben_id
    lookup, absent means a Mobile lookup.
    """
    attributes: Dict[str, Any] = {
        "@context": scheme_application_context(),
        "@type": SCHEME_APPLICATION_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
    }
    if reg_no:
        attributes["enrolmentId"] = reg_no
    else:
        attributes["applicant"] = {"mobile": phone_number}
    return attributes


def clean_otp(otp: str) -> str:
    """The four digits, or "" when it is not four digits."""
    value = to_ascii_digits(otp or "").strip()
    return value if value.isdigit() and len(value) == 4 else ""


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def _ist_date(value: Optional[str]) -> str:
    """The date half of an IST timestamp, as DD-MM-YYYY."""
    if not value:
        return ""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return str(value)


def format_application(attributes: Dict[str, Any]) -> str:
    """The application block as lines a farmer can be read.

    Only the fields the v0.1 pack can hold appear here. v1 showed whatever the
    gateway chose to put in its tags, which was sometimes more and sometimes
    less; the two are not line-for-line comparable.
    """
    application = attributes.get("application") or {}
    lines: List[str] = []

    enrolment = attributes.get("enrolmentId")
    if enrolment:
        lines.append(f"Registration Number: {enrolment}")

    registered_on = _ist_date(application.get("registeredOn"))
    if registered_on:
        lines.append(f"Registered On: {registered_on}")

    instalment = application.get("latestInstalmentPaid")
    if instalment is not None:
        lines.append(f"Latest Instalment Paid: {instalment}")

    ekyc = (application.get("ekyc") or {}).get("code")
    if ekyc:
        lines.append(f"eKYC: {ekyc}")

    # Three states, not two: a list of blockers, an empty list (PM-KISAN
    # checked and found none), and no key at all (the follow-up read failed,
    # ADR-0005). A null is the third, not the second. See plan.md.
    blockers = application.get("blockers")
    if blockers:
        lines.append("")
        lines.append("Payment is currently blocked by:")
        for blocker in blockers:
            name = (blocker.get("name") or blocker.get("code") or "").strip()
            if name:
                lines.append(f"- {name}")
    elif blockers == []:
        lines.append("")
        lines.append("Nothing is blocking payment.")

    if not lines:
        return "PM-KISAN returned no application details. Please try again later."
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:initiate_pm_kisan_status_check", as_type="tool")
async def initiate_pm_kisan_status_check(
    ctx: RunContext[FarmerContext], reg_no: str = "", phone_number: str = ""
) -> str:
    """Initiate PM Kisan status check by sending OTP to farmer's mobile.

    Use this tool to initiate the process to check the status of a farmer's scheme benefit by sending an OTP to their registered mobile number.
    The farmer can provide either their PM Kisan registration number OR their registered phone number.

    Args:
        reg_no (str): PM Kisan registration number: 2 letters followed by 9 digits (11 characters, not 11 digits), for example TN000000000 (dummy format example only). Leave empty if phone_number is provided.
        phone_number (str): Farmer's registered mobile number (10 digits). Leave empty if reg_no is provided.

    Returns:
        str: Response from the scheme status check service
    """
    reg_no, phone_number = split_identifier(reg_no, phone_number)
    if not (reg_no or phone_number):
        return _NO_IDENTIFIER_MSG

    try:
        transaction_id = generate_transaction_id(
            ctx.deps.session_id, reg_no or phone_number
        )
        lf_update_current_observation(
            metadata={
                "tool": "pmkisan.init",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_application()
        if not found:
            logger.warning("PM-KISAN v2 discover found no application-status catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "init",
            transaction_id,
            DRAFT,
            applicant_attributes(reg_no, phone_number),
            _APPLICATION_OFFER,
            found,
        )
        response = await post_action("init", body, who="PM-KISAN")
        if response.status_code != 200:
            return explain_failure(response, "OTP request")

        issued = (commitment_attributes(response).get("challenge") or {}).get("issued") or {}
        length = issued.get("length") or 4
        return f"{_OTP_SENT_MSG} Please share the {length}-digit OTP to continue."

    except httpx.TimeoutException:
        logger.error("PM-KISAN v2 init timed out")
        return "Scheme init request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PM-KISAN v2 init failed: {e}")
        return f"Scheme init request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("PM-KISAN v2 init exceeded retry limit")
        return "Scheme init service is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in PM-KISAN v2 init: {e}")
        raise ModelRetry(f"Unexpected error in scheme init request. {str(e)}")


@observe(name="tool:check_pm_kisan_status_with_otp", as_type="tool")
async def check_pm_kisan_status_with_otp(
    ctx: RunContext[FarmerContext], otp: str, reg_no: str = "", phone_number: str = ""
) -> str:
    """Check PM Kisan status using OTP after initiating the OTP check.

    Use this tool to check the status of a farmer's benefit using the OTP received via SMS after calling initiate_pm_kisan_status_check.
    The farmer can provide either their PM Kisan registration number OR their registered phone number.

    Args:
        otp (str): A 4 digit OTP received via SMS
        reg_no (str): PM Kisan registration number: 2 letters followed by 9 digits (11 characters, not 11 digits), for example TN000000000 (dummy format example only). Leave empty if phone_number is provided.
        phone_number (str): Farmer's registered mobile number (10 digits). Leave empty if reg_no is provided.

    Returns:
        str: Detailed scheme status information including beneficiary details, payment status, and any issues or next steps
    """
    reg_no, phone_number = split_identifier(reg_no, phone_number)
    if not (reg_no or phone_number):
        return _NO_IDENTIFIER_MSG

    try:
        otp_clean = clean_otp(otp)
        if not otp_clean:
            raise ModelRetry(
                "Invalid OTP format. Please provide a 4-digit OTP received via SMS."
            )

        transaction_id = generate_transaction_id(
            ctx.deps.session_id, reg_no or phone_number
        )
        lf_update_current_observation(
            metadata={
                "tool": "pmkisan.status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        attributes = applicant_attributes(reg_no, phone_number)
        attributes["challenge"] = {"response": {"method": "OTP", "value": otp_clean}}
        found = await _discover_application()
        if not found:
            logger.warning("PM-KISAN v2 discover found no application-status catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "status",
            transaction_id,
            ACTIVE,
            attributes,
            _APPLICATION_OFFER,
            found,
        )
        response = await post_action("status", body, who="PM-KISAN")
        if response.status_code != 200:
            return explain_failure(response, "status check")

        return format_application(commitment_attributes(response))

    except httpx.TimeoutException:
        logger.error("PM-KISAN v2 status timed out")
        return "Scheme status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PM-KISAN v2 status failed: {e}")
        return f"Scheme status request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except UnexpectedModelBehavior:
        logger.warning("PM-KISAN v2 status exceeded retry limit")
        return "Scheme status service is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in PM-KISAN v2 status: {e}")
        raise ModelRetry(f"Unexpected error in scheme status request. {str(e)}")
