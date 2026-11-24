"""
PMFBY grievance tools over Beckn v2.

Two of the six v1 PMFBY tools move here. The other four have no v2 target and
stay on v1 -- see migration-plan/pmfby/plan.md.

    pmfby_grievance_status    v1 /search  ->  v2 status   (no OTP, either version)
    pmfby_submit_grievance    v1 /init    ->  v2 support  (OTP gate unchanged)

**The OTP gate is kept exactly where v1 put it.** `_verify_pmfby_otp_with_bap`
is a v1 BAP /status round-trip and stays on v1, because the Grievance/v0.1 pack
has no PMFBYInit for it to become. pmfby_submit_grievance calls it before it
builds the support body and returns its failure message unchanged, so the v2
path is no weaker than the v1 path. Moving the gate into the network -- init
issuing a challenge, support carrying challenge.response, the way PM-KISAN
works -- needs a pack addition and is the TODO in the plan.

PMFBY never sees the OTP on either version. What verifies is our own tool, not
the portal.

The output is NOT byte-comparable with v1. v1 rendered the gateway's tag soup;
v2 reads named fields out of the v0.1 pack and renders them here.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.pmfby_grievance import (
    _normalize_request_season_for_pmfby_api,
    _require_nonempty,
    _validate_registered_mobile,
    _verify_pmfby_otp_with_bap,
    generate_transaction_id,
)
from agents.tools.v2.pmkisan_scheme_status import (
    ACTIVE,
    GRIEVANCE_CONTEXT,
    GRIEVANCE_TYPE,
    _first_resource,
    adapter_error,
    adapter_url,
    build_context,
    commitment_attributes,
    commitment_envelope,
)
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

SCHEME = {"code": "PMFBY"}
PROVIDER_NAME = "PMFBY Grievance Portal"

# discover returns catalogs and resources, never offers, so this stays a
# constant; the provider and the resource id come off the wire.
_GRIEVANCE_OFFER = "off:pmfby:grievance"

# The pack's own enum, and not the same third season v1 sends the BAP: v1's
# status call says "Summer", the Grievance pack says "Zaid".
_SEASON_LABELS = {"1": "Kharif", "2": "Rabi", "3": "Zaid"}

_UNAVAILABLE_MSG = (
    "The PMFBY grievance service is not available right now. "
    "Please try again later."
)


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------

async def _post(action: str, body: Dict[str, Any]) -> httpx.Response:
    url = adapter_url(action)
    logger.info(f"PMFBY v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    logger.info(f"PMFBY v2 {action} <- {response.status_code}")
    return response


def _explain_failure(response: httpx.Response, what: str) -> str:
    """What to tell the farmer when the adapter refused.

    A BIZ_ error is PMFBY's own answer, already written to be read aloud --
    "no grievance was found for this ticket number", "the phone number does not
    match". Anything else is ours or the network's.
    """
    code, message = adapter_error(response)
    logger.error(
        f"PMFBY v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if code.startswith("BIZ_") and message:
        return message
    if code.startswith("SCH_"):
        return f"PMFBY {what} could not be completed. Please try again later."
    return f"PMFBY {what} is temporarily unavailable. Please try again later."


async def _discover_grievance() -> Optional[Dict[str, Any]]:
    """The PMFBY grievance resource, with its catalog provider.

    The scheme code is not a nicety: PM-KISAN publishes openagrinet:Grievance
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
    response = await _post("discover", body)
    if response.status_code != 200:
        logger.error(
            f"PMFBY v2 grievance discover failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
        return None
    return _first_resource(response.json(), who="PMFBY")


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------

def _season_label(season: str) -> str:
    """The farmer's season as the pack's enum, or "" to ask again."""
    return _SEASON_LABELS.get(_normalize_request_season_for_pmfby_api(season), "")


def _crop_year(value: str) -> str:
    """A four-digit crop year, or "" to ask again. The pack bounces anything else."""
    digits = to_ascii_digits(value or "").strip()
    return digits if len(digits) == 4 and digits.isdigit() else ""


def _application_no(value: str) -> str:
    """PMFBY's own application number, as the pack sizes it.

    Ten to thirty characters and never twelve digits: that shape is an Aadhaar
    number, and the pack refuses it rather than let one reach the portal.
    """
    text = to_ascii_digits(value or "").strip()
    if len(text) < 10 or len(text) > 30:
        return ""
    if len(text) == 12 and text.isdigit():
        return ""
    return text


def _display_date(iso: str) -> str:
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return iso or ""


def _grievance_attributes(mobile: str) -> Dict[str, Any]:
    return {
        "@context": GRIEVANCE_CONTEXT,
        "@type": GRIEVANCE_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "applicantMobile": mobile,
    }


def _format_case(attributes: Dict[str, Any]) -> str:
    """The grievance as lines a farmer can be read."""
    case = attributes.get("case") or {}
    grievance = attributes.get("grievance") or {}
    lines = []

    if case.get("ticketNo"):
        lines.append(f"Grievance Ticket Number: {case['ticketNo']}")
    if attributes.get("enrolmentId"):
        lines.append(f"Application Number: {attributes['enrolmentId']}")
    if attributes.get("season") or attributes.get("cropYear"):
        season = " ".join(
            str(part) for part in (attributes.get("season"), attributes.get("cropYear")) if part
        )
        lines.append(f"Season: {season}")
    if case.get("cropName"):
        lines.append(f"Crop: {case['cropName']}")
    if (case.get("status") or {}).get("code"):
        lines.append(f"Grievance Status: {case['status']['code']}")
    if case.get("filedOn"):
        lines.append(f"Grievance Date: {_display_date(case['filedOn'])}")
    if (grievance.get("category") or {}).get("name"):
        lines.append(f"Grievance Category: {grievance['category']['name']}")
    if grievance.get("description"):
        lines.append(f"Grievance Description: {grievance['description']}")
    if case.get("reply"):
        lines.append(f"Officer Reply: {case['reply']}")

    if not lines:
        return "PMFBY returned no grievance details. Please try again later."
    return "\n".join(lines)


def _first_channel(response: httpx.Response) -> Dict[str, Any]:
    """The first channel off an on_support response."""
    try:
        channels = (
            response.json().get("message", {}).get("support", {}).get("channels") or []
        )
    except ValueError:
        return {}
    return channels[0] if channels else {}


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:pmfby_grievance_status", as_type="tool")
async def pmfby_grievance_status(
    ctx: RunContext[FarmerContext],
    phone_number: str,
    grievance_support_ticket_no: str,
) -> str:
    """Check PMFBY grievance status for an existing support ticket.

    Ask the farmer for their registered mobile number and grievance support ticket number,
    then call this tool. No OTP is required for status lookup.

    Args:
        phone_number: Mobile number registered with PMFBY (10 digits).
        grievance_support_ticket_no: Grievance support ticket number (same value as message order_id).

    Returns:
        Grievance status details from the PMFBY grievance portal.
    """
    try:
        phone = _validate_registered_mobile(phone_number)
        ticket = to_ascii_digits(
            _require_nonempty(
                grievance_support_ticket_no,
                "Please share your PMFBY grievance support ticket number.",
            )
        ).strip()

        transaction_id = generate_transaction_id(ctx.deps.session_id, f"{phone}:{ticket}")
        lf_update_current_observation(
            metadata={
                "tool": "pmfby_grievance.status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_grievance()
        if not found:
            logger.warning("PMFBY v2 discover found no grievance catalog")
            return _UNAVAILABLE_MSG

        attributes = _grievance_attributes(phone)
        attributes["case"] = {"ticketNo": ticket}
        body = commitment_envelope(
            "status",
            transaction_id,
            ACTIVE,
            attributes,
            _GRIEVANCE_OFFER,
            found,
        )
        response = await _post("status", body)
        if response.status_code != 200:
            return _explain_failure(response, "grievance status check")

        return _format_case(commitment_attributes(response))

    except httpx.TimeoutException:
        logger.error("PMFBY v2 grievance status check timed out.")
        return "PMFBY grievance status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PMFBY v2 grievance status network error: {e}")
        return "Unable to reach the PMFBY grievance service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Error in pmfby_grievance_status (v2): {e}")
        raise ModelRetry(f"Unexpected error checking PMFBY grievance status. {str(e)}") from e


@observe(name="tool:pmfby_submit_grievance", as_type="tool")
async def pmfby_submit_grievance(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    request_year: str,
    request_season: str,
    application_no: str,
    grievance_description: str,
) -> str:
    """Submit PMFBY grievance via Beckn `support` after OTP verification.

    Call only after `check_pmfby_grievance_otp` returned OTP verified and the farmer
    has provided application number, season/year, and complaint description.

    Args:
        otp: 6-digit OTP received on the registered mobile. Required.
        phone_number: Mobile number registered with PMFBY (10 digits).
        request_year: Crop year of the policy, four digits.
        request_season: Kharif, Rabi, or Zaid / summer. Pass the season name, not a code.
        application_no: PMFBY application number the grievance is about.
        grievance_description: What the farmer is complaining about.

    Returns:
        The registered ticket, or the reason PMFBY refused.
    """
    try:
        phone = _validate_registered_mobile(phone_number)

        year = _crop_year(_require_nonempty(request_year, "Please share the request year."))
        if not year:
            raise ModelRetry(
                "Please share the crop year of the PMFBY policy as four digits, for example 2025."
            )

        raw_season = _require_nonempty(request_season, "Please share the request season.")
        season = _season_label(raw_season)
        if not season:
            raise ModelRetry(
                "Ask which crop season the PMFBY policy is for — **Kharif**, **Rabi**, or "
                "**Zaid / summer** (or the same in the farmer's language). Do not ask them to "
                "memorize number codes; pass the season name to this tool."
            )

        # The gate, unchanged from v1: a separate v1 /status round-trip that must
        # pass before anything is filed. See the module docstring.
        verified, otp_message = _verify_pmfby_otp_with_bap(
            ctx=ctx,
            otp=otp,
            phone_number=phone_number,
            year=year,
            season=raw_season,
        )
        if not verified:
            return otp_message

        enrolment_id = _application_no(
            _require_nonempty(application_no, "Please share your PMFBY application number.")
        )
        if not enrolment_id:
            raise ModelRetry(
                "That does not look like a PMFBY application number. Please share the "
                "application number printed on the policy receipt."
            )

        if not grievance_description or len(grievance_description.strip()) < 10:
            raise ModelRetry(
                "Please provide a brief grievance description (at least 10 characters)."
            )

        transaction_id = generate_transaction_id(ctx.deps.session_id, phone)
        lf_update_current_observation(
            metadata={
                "tool": "pmfby_grievance.submit",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_grievance()
        if not found:
            logger.warning("PMFBY v2 discover found no grievance catalog")
            return _UNAVAILABLE_MSG

        # support carries its payload on channels, not on a contract. The three
        # ticket constants v1 hardcodes are the mapping's defaults, so they are
        # not sent from here.
        body = {
            "context": build_context("support", transaction_id),
            "message": {
                "support": {
                    "channels": [
                        {
                            **_grievance_attributes(phone),
                            "provider": {
                                "id": found["provider"].get("id"),
                                "descriptor": {"name": PROVIDER_NAME},
                            },
                            "enrolmentId": enrolment_id,
                            "cropYear": year,
                            "season": season,
                            "grievance": {"description": grievance_description.strip()},
                        }
                    ]
                }
            },
        }
        response = await _post("support", body)
        if response.status_code != 200:
            return _explain_failure(response, "grievance submission")

        channel = _first_channel(response)
        case = channel.get("case") or {}
        if not case.get("ticketNo"):
            return (
                "PMFBY accepted the grievance but returned no ticket number. "
                "Please try again later."
            )

        lines = ["Your PMFBY grievance has been registered."]
        lines.append(f"Grievance Ticket Number: {case['ticketNo']}")
        if case.get("filedOn"):
            lines.append(f"Grievance Date: {_display_date(case['filedOn'])}")
        lines.append(
            "Please keep this ticket number — it is needed to check the grievance later."
        )
        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error("PMFBY v2 grievance submission timed out.")
        return "PMFBY grievance request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PMFBY v2 grievance submission network error: {e}")
        return "Unable to reach the PMFBY grievance service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Error in pmfby_submit_grievance (v2): {e}")
        raise ModelRetry(f"Unexpected error submitting PMFBY grievance. {str(e)}") from e
