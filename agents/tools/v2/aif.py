"""
AIF (Agriculture Infrastructure Fund) loan and grievance status over Beckn v2.

Same four tools, same arguments, same purpose as agents.tools.aif -- only the
transport changes. v1 posted /init and /status at the BAP and read the answer
out of tag soup; v2 posts the same two actions at the consumer adapter against
two capabilities:

    init    (no challenge)      -> AIF sends a six-digit OTP
    init    (with challenge)    -> the OTP is spent and a session is bought
    status  SchemeApplication   -> one loan application's status
    status  Grievance           -> the beneficiary's support tickets

**The session is held by the adapter, not here.** Verifying the OTP buys a
bearer token that the adapter keeps in Redis against the transaction id and
sends on every later status call. It is never in a response and must never be
asked for. That is why all four calls share one transaction id, derived from
the session and the beneficiary exactly as v1 derived it -- a farmer who
verified in this conversation stays verified for it, and a different
beneficiary gets a different session.

No round trips are saved -- both versions make the same four calls. What
changes is that the token stops travelling: v1 carried it back to the agent
and re-presented it on every status call, v2 leaves it in the adapter.

The output is NOT byte-comparable with v1. v1 rendered whatever tags the
gateway returned; v2 reads named fields out of the v0.2 pack. Read the two
side by side rather than diffing them. See migration-plan/aif/plan.md.
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
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

# v0.2, not v0.1. AIF is the first capability on the newer packs; PM-KISAN and
# PMFBY stay on v0.1 and are validated against their own packs in the same
# adapter, because the validator resolves a pack from the payload's @context.
#
# The base is overridable because v0.2 is not published yet: until it is, a
# deployment serves the packs itself and names that host in the adapter's
# EXTRA_SCHEMA_DOMAINS. Drop the override the day v0.2 is on the public host.
def schema_base() -> str:
    return os.getenv(
        "OAN_SCHEMA_BASE", "https://openagrinet.github.io/network-specs/schema"
    ).rstrip("/")


def scheme_application_context() -> str:
    return f"{schema_base()}/SchemeApplication/v0.2/context.jsonld"


def grievance_context() -> str:
    return f"{schema_base()}/Grievance/v0.2/context.jsonld"


SCHEME_APPLICATION_TYPE = "openagrinet:SchemeApplication"
GRIEVANCE_TYPE = "openagrinet:Grievance"

SCHEME = {"code": "AIF"}
SOURCE_LINE = "**Source:** AIF Portal"

# Which catalog audience to discover in -- not who we are, senderId is that.
# visibleTo is matched by array overlap and AIF's catalog carries "*", so "*"
# is what finds it. Same selector weather, mandi and sathi use.
_DISCOVER_NETWORK_ID = os.getenv("OAN_PUBLIC_NETWORK_ID", "*")

# Every AIF catalog resource carries one of these descriptor codes. @type is
# openagrinet:AgricultureResource on both, so it cannot tell them apart.
_LOAN_CODE = "aif-loan-status"
_TICKETS_CODE = "aif-support-tickets"

# discover returns catalogs and resources, never offers, so these stay
# constants. The provider and the resource ids come off the wire.
_LOAN_OFFER = "off:aif:loan-status"
_TICKETS_OFFER = "off:aif:tickets"

_UNAVAILABLE_MSG = (
    "The AIF portal is not available right now. Please try again later."
)

DRAFT = "DRAFT"
ACTIVE = "ACTIVE"

NEED_BENEFICIARY_ID = "Ask the farmer for their AIF beneficiary ID (digits only)."
NEED_OTP = "Ask the farmer for the 6-digit OTP sent to their registered mobile."
NEED_LOAN_NUMBER = "Ask the farmer for their AIF loan application number (digits only)."


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------

def adapter_url(action: str) -> str:
    base = os.getenv("OAN_ADAPTER_URL", "http://network-adapter:9201/consumer")
    return base.rstrip("/") + "/" + action


def build_context(action: str, transaction_id: str) -> Dict[str, Any]:
    """One network id on all three fields: a single-network deployment."""
    network_id = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return {
        "domain": os.getenv("OAN_DOMAIN", "agriculture"),
        "action": action,
        "version": "2.0.0",
        "networkId": _DISCOVER_NETWORK_ID if action == "discover" else network_id,
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
) -> Dict[str, Any]:
    """The one contract-with-one-commitment shape every AIF action uses.

    `found` is what a _discover_* call returned: the catalog's provider and the
    resource it published.
    """
    resource_id = found["resource"]["id"]
    return {
        "context": build_context(action, transaction_id),
        "message": {
            "contract": {
                "id": str(uuid.uuid4()),
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

    A BIZ_ error is AIF's own answer and is written to be read aloud. An SCH_
    error is ours -- except for the two the session layer raises, which are the
    farmer's to act on: a session that expired, and a question about a
    beneficiary other than the verified one. Both mean "verify again", so they
    are passed through rather than buried.
    """
    code, message = adapter_error(response)
    logger.error(
        f"AIF v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if code.startswith("BIZ_") and message:
        return message
    if "verified session" in message or "different beneficiary" in message:
        return (
            "This AIF check needs a fresh verification. "
            "Send a new OTP and ask the farmer to read it out."
        )
    if code.startswith("SCH_"):
        return f"AIF {what} could not be completed. Please try again later."
    return f"AIF {what} is temporarily unavailable. Please try again later."


async def post_action(action: str, body: Dict[str, Any]) -> httpx.Response:
    url = adapter_url(action)
    logger.info(f"AIF v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    logger.info(f"AIF v2 {action} <- {response.status_code}")
    return response


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

async def _discover(descriptor_code: str, action: str, what: str) -> Optional[Dict[str, Any]]:
    """The AIF resource for a capability, with its catalog provider.

    AIF publishes both capabilities as openagrinet:AgricultureResource, so
    @type cannot tell them apart; the descriptor code can, which is how
    sathi_seed finds its own resources.
    """
    body = {
        "context": build_context("discover", str(uuid.uuid4())),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        f'@.descriptor.code == "{descriptor_code}")'
                    ),
                }
            }
        },
    }
    response = await post_action("discover", body)
    if response.status_code != 200:
        logger.error(
            f"AIF v2 {what} discover failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
        return None
    found = _first_resource(response.json())
    if found and not _offers(found, action):
        logger.error(
            f"AIF v2 {found['resource'].get('id')} does not advertise {action}; "
            f"catalog offers {[a.get('action') for a in _actions(found)]}"
        )
        return None
    return found


def _first_resource(payload: dict) -> Optional[Dict[str, Any]]:
    """Nothing to rank: AIF publishes one resource per capability."""
    for catalog in (payload.get("message") or {}).get("catalogs") or []:
        provider = catalog.get("provider") or {}
        for resource in catalog.get("resources") or []:
            return {"resource": resource, "provider": provider}
    return None


def _actions(found: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (found["resource"].get("resourceAttributes") or {}).get("actions") or []


def _offers(found: Dict[str, Any], action: str) -> bool:
    """Whether the published resource advertises the action about to be sent."""
    return any(entry.get("action") == action for entry in _actions(found))


async def _discover_loan(action: str) -> Optional[Dict[str, Any]]:
    return await _discover(_LOAN_CODE, action, "loan")


async def _discover_tickets(action: str) -> Optional[Dict[str, Any]]:
    return await _discover(_TICKETS_CODE, action, "tickets")


def numeric(value: str) -> str:
    """Normalises regional-script digits; "" when it is not a plain run of digits."""
    cleaned = to_ascii_digits(str(value or "")).strip().replace(" ", "")
    return cleaned if re.fullmatch(r"\d+", cleaned) else ""


def scheme_attributes(beneficiary_id: str, **extra: Any) -> Dict[str, Any]:
    """The SchemeApplication container. beneficiaryId is a STRING on the wire:
    the adapter's session binding compares strings, and the mapping casts it to
    the integer AIF wants."""
    return {
        "@context": scheme_application_context(),
        "@type": SCHEME_APPLICATION_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "beneficiaryId": beneficiary_id,
        **extra,
    }


def grievance_attributes(beneficiary_id: str) -> Dict[str, Any]:
    return {
        "@context": grievance_context(),
        "@type": GRIEVANCE_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "beneficiaryId": beneficiary_id,
    }


def clean_otp(otp: str) -> str:
    """The six digits, or "" when it is not six digits. AIF rejects anything
    else, so this is caught before the round trip rather than after it."""
    value = numeric(otp)
    return value if len(value) == 6 else ""


def aif_transaction_id(ctx: RunContext[FarmerContext], beneficiary_id: str) -> str:
    """One id for all four calls of one conversation about one beneficiary.

    This is what ties the status calls to the OTP: the adapter keyed the
    session on it. Deriving it the same way v1 did means a conversation that
    starts on one version and is restarted on the other lands on the same id.
    """
    return generate_transaction_id(ctx.deps.session_id, beneficiary_id)


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def shorten(text: str, limit: int = 120) -> str:
    """Collapses whitespace and caps a ticket description; farmers write long run-ons."""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip(" ,.;") + "…"


def format_loan_status(attributes: Dict[str, Any]) -> str:
    """Name the application explicitly -- a beneficiary can hold several, and
    the agent must not attach a status to the wrong one."""
    status = (attributes.get("application") or {}).get("status") or {}
    state = status.get("name") or status.get("code")
    if not state:
        return "AIF returned no status for that loan application. Please try again later."
    number = attributes.get("loanApplicationNumber")
    if number:
        return f"{SOURCE_LINE}\n\nLoan application {number}: {state}"
    return f"{SOURCE_LINE}\n\n{state}"


def group_cases(cases: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Tickets keyed by application number, in first-seen order. A farmer
    tracks their applications, and one application can carry several tickets.
    Tickets tied to no application collect under ""."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case.get("loanApplicationNumber") or ""), []).append(case)
    return grouped


def group_lines(key: str, cases: List[Dict[str, Any]]) -> List[str]:
    header = f"Application {key}" if key else "Not linked to an application"
    lines = [f"{header} — {len(cases)} ticket(s)"]
    for case in cases:
        kind = (case.get("category") or {}).get("name") or case.get("subject") or "Support ticket"
        status = (case.get("status") or {}).get("name") or "Unknown"
        lines.append(f"  {kind} ({status})")
        description = shorten(case.get("description") or "")
        if description:
            lines.append(f"    {description}")
    lines.append("")
    return lines


def format_cases(attributes: Dict[str, Any]) -> str:
    """`cases` is always an array, empty included: AIF checked and found none is
    an answer a farmer can be given, and a missing key is one they cannot."""
    cases = attributes.get("cases")
    if cases is None:
        return "AIF could not list the support tickets right now. Please try again later."
    if not cases:
        return f"{SOURCE_LINE}\n\nThere are no open grievances or support tickets."

    grouped = group_cases(cases)
    numbered = [key for key in grouped if key]
    summary = f"{len(cases)} support ticket(s)"
    if numbered:
        summary += f" across {len(numbered)} application(s)"

    lines = [SOURCE_LINE, "", f"{summary}.", ""]
    for key in numbered + ([""] if "" in grouped else []):
        lines.extend(group_lines(key, grouped[key]))
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:initiate_aif_otp", as_type="tool")
async def initiate_aif_otp(ctx: RunContext[FarmerContext], beneficiary_id: str) -> str:
    """Send an OTP to the AIF beneficiary's registered mobile.

    First step for any Agriculture Infrastructure Fund (AIF) loan or grievance status
    check.

    Args:
        beneficiary_id (str): AIF beneficiary ID, digits only (e.g. "106545").

    Returns:
        str: Confirmation that the OTP was sent, or the reason it failed.
    """
    beneficiary_id = numeric(beneficiary_id)
    if not beneficiary_id:
        return NEED_BENEFICIARY_ID

    try:
        transaction_id = aif_transaction_id(ctx, beneficiary_id)
        lf_update_current_observation(
            metadata={
                "tool": "aif.get_otp",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_loan("init")
        if not found:
            logger.warning("AIF v2 discover found no loan catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "init",
            transaction_id,
            DRAFT,
            scheme_attributes(beneficiary_id),
            _LOAN_OFFER,
            found,
        )
        response = await post_action("init", body)
        if response.status_code != 200:
            return explain_failure(response, "OTP request")

        issued = (commitment_attributes(response).get("challenge") or {}).get("issued") or {}
        length = issued.get("length") or 6
        lines = [
            "An OTP has been sent to the mobile number registered with AIF. "
            f"Please share the {length}-digit OTP to continue."
        ]
        # The prompt tells the agent to read the masked number back to the farmer,
        # so a farmer with two phones knows which one to check.
        if issued.get("sentTo"):
            lines.append(f"Registered mobile: {issued['sentTo']}")
        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error("AIF v2 init timed out")
        return "AIF OTP request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"AIF v2 init failed: {e}")
        return f"AIF OTP request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("AIF v2 init exceeded retry limit")
        return "The AIF system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in AIF v2 init: {e}")
        raise ModelRetry(f"Unexpected error in AIF OTP request. {str(e)}")


@observe(name="tool:verify_aif_otp", as_type="tool")
async def verify_aif_otp(
    ctx: RunContext[FarmerContext], otp: str, beneficiary_id: str
) -> str:
    """Verify the AIF OTP. Call after initiate_aif_otp, before any AIF status check.

    One verification covers both check_aif_loan_status and check_aif_grievance_status
    for the rest of the conversation, so the OTP is submitted exactly once.

    Args:
        otp (str): The 6-digit OTP the farmer received by SMS.
        beneficiary_id (str): The same AIF beneficiary ID used for initiate_aif_otp.

    Returns:
        str: Verification result, or the reason it failed.
    """
    beneficiary_id = numeric(beneficiary_id)
    if not beneficiary_id:
        return NEED_BENEFICIARY_ID

    otp_clean = clean_otp(otp)
    if not otp_clean:
        return NEED_OTP

    try:
        transaction_id = aif_transaction_id(ctx, beneficiary_id)
        lf_update_current_observation(
            metadata={
                "tool": "aif.verify_otp",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_loan("init")
        if not found:
            logger.warning("AIF v2 discover found no loan catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "init",
            transaction_id,
            DRAFT,
            scheme_attributes(
                beneficiary_id,
                challenge={"response": {"method": "OTP", "value": otp_clean}},
            ),
            _LOAN_OFFER,
            found,
        )
        response = await post_action("init", body)
        if response.status_code != 200:
            return explain_failure(response, "OTP verification")

        # The token the adapter just stored is deliberately absent from this
        # answer. Report the verification and the name AIF confirmed, nothing more.
        name = (commitment_attributes(response).get("applicant") or {}).get("name")
        if name:
            return f"OTP verified for {name}. You can now check loan or grievance status."
        return "OTP verified. You can now check loan or grievance status."

    except httpx.TimeoutException:
        logger.error("AIF v2 verify timed out")
        return "AIF OTP verification timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"AIF v2 verify failed: {e}")
        return f"AIF OTP verification failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("AIF v2 verify exceeded retry limit")
        return "The AIF system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in AIF v2 verify: {e}")
        raise ModelRetry(f"Unexpected error in AIF OTP verification. {str(e)}")


@observe(name="tool:check_aif_loan_status", as_type="tool")
async def check_aif_loan_status(
    ctx: RunContext[FarmerContext], beneficiary_id: str, loan_application_number: str
) -> str:
    """Check the status of an AIF loan application. Requires a verified OTP.

    Args:
        beneficiary_id (str): The same AIF beneficiary ID used for verify_aif_otp.
        loan_application_number (str): AIF loan application number, digits only. Lengths vary.

    Returns:
        str: The loan application status, or the reason it failed.
    """
    beneficiary_id = numeric(beneficiary_id)
    if not beneficiary_id:
        return NEED_BENEFICIARY_ID

    loan_application_number = numeric(loan_application_number)
    if not loan_application_number:
        return NEED_LOAN_NUMBER

    try:
        transaction_id = aif_transaction_id(ctx, beneficiary_id)
        lf_update_current_observation(
            metadata={
                "tool": "aif.loan_status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_loan("status")
        if not found:
            logger.warning("AIF v2 discover found no loan catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "status",
            transaction_id,
            ACTIVE,
            scheme_attributes(
                beneficiary_id, loanApplicationNumber=loan_application_number
            ),
            _LOAN_OFFER,
            found,
        )
        response = await post_action("status", body)
        if response.status_code != 200:
            return explain_failure(response, "loan status check")

        return format_loan_status(commitment_attributes(response))

    except httpx.TimeoutException:
        logger.error("AIF v2 loan status timed out")
        return "AIF loan status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"AIF v2 loan status failed: {e}")
        return f"AIF loan status request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("AIF v2 loan status exceeded retry limit")
        return "The AIF system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in AIF v2 loan status: {e}")
        raise ModelRetry(f"Unexpected error in AIF loan status request. {str(e)}")


@observe(name="tool:check_aif_grievance_status", as_type="tool")
async def check_aif_grievance_status(
    ctx: RunContext[FarmerContext], beneficiary_id: str
) -> str:
    """Check AIF support tickets / grievances for a beneficiary. Requires a verified OTP.

    Args:
        beneficiary_id (str): The same AIF beneficiary ID used for verify_aif_otp.

    Returns:
        str: The support tickets and their statuses, or the reason it failed.
    """
    beneficiary_id = numeric(beneficiary_id)
    if not beneficiary_id:
        return NEED_BENEFICIARY_ID

    try:
        transaction_id = aif_transaction_id(ctx, beneficiary_id)
        lf_update_current_observation(
            metadata={
                "tool": "aif.grievance_status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover_tickets("status")
        if not found:
            logger.warning("AIF v2 discover found no tickets catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "status",
            transaction_id,
            ACTIVE,
            grievance_attributes(beneficiary_id),
            _TICKETS_OFFER,
            found,
        )
        response = await post_action("status", body)
        if response.status_code != 200:
            return explain_failure(response, "grievance status check")

        return format_cases(commitment_attributes(response))

    except httpx.TimeoutException:
        logger.error("AIF v2 grievance status timed out")
        return "AIF grievance status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"AIF v2 grievance status failed: {e}")
        return f"AIF grievance status request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("AIF v2 grievance status exceeded retry limit")
        return "The AIF system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in AIF v2 grievance status: {e}")
        raise ModelRetry(f"Unexpected error in AIF grievance status request. {str(e)}")
