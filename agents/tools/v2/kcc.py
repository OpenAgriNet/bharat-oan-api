"""
KCC (Kisan Credit Card) application status over Beckn v2.

Same three tools, same arguments, same purpose as agents.tools.kcc -- only the
transport changes. v1 posted /init and /status at the BAP and read the answer
out of tag soup; v2 posts two actions at the consumer adapter against one
SchemeApplication/v0.2 capability:

    init    -> KCC sends a six-digit OTP and answers with a requestId
    status  -> the OTP is answered and every application on that mobile comes
               back in the SAME call

There is no verify action, because KCC has no verify endpoint. A wrong OTP is
refused on status, which is why the third tool, select_kcc_application, sends
nothing at all: it picks a row out of the applications status already returned,
so choosing between several does not spend a second OTP.

**The session is held by the adapter, not here.** What this module holds is the
requestId: KCC wants it inside the body of the status call, and the adapter
cannot put a held value into a body it did not build. Holding it is also what
lets a farmer answer the OTP once and then browse their applications.

The output is NOT byte-comparable with v1. v1 rendered whatever tags the
gateway returned; v2 reads named fields out of the v0.2 pack. See
migration-plan/kcc/plan.md.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.pmkisan_scheme_status import generate_transaction_id
from agents.tools.v2.aif import (
    ACTIVE,
    DRAFT,
    _actions,
    _first_resource,
    adapter_error,
    build_context,
    commitment_attributes,
    commitment_envelope,
    numeric,
    post_action,
    scheme_application_context,
    SCHEME_APPLICATION_TYPE,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

SCHEME = {"code": "KCC", "name": "Kisan Credit Card"}
SOURCE_LINE = "**Source:** Kisan Rin Portal"

# The catalog publishes one resource under this code; the offer is not on the
# discover answer, so it stays a constant while provider and resource id come
# off the wire.
_LOAN_CODE = "kcc-loan"
_LOAN_OFFER = "off:kcc:loan"

UNAVAILABLE = "The KCC system cannot be reached right now. Please try again in a few minutes."
NEED_MOBILE = "Ask the farmer for the 10-digit mobile number they used for their KCC application."
NEED_OTP = "Ask the farmer for the 6-digit OTP sent to their mobile."
NEED_APPLICATION_NO = "Ask the farmer which KCC application number to check (digits only)."
NEED_FRESH_OTP = (
    "This KCC check needs a fresh OTP. Send a new one and ask the farmer to read it out."
)

# KCC's requestId is good for five minutes (sessionTtl on the binding), so one
# older than that cannot be spent anyway. The applications outlive it: they are
# already in hand, and select_kcc_application only reads them back.
_REQUEST_ID_TTL_SECONDS = 5 * 60
_APPLICATIONS_TTL_SECONDS = 15 * 60
_request_ids: Dict[str, Tuple[str, float]] = {}
_applications: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}

APPROVED_STATUSES = {"APPROVED", "SANCTIONED", "DISBURSED"}


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------

async def _post(action: str, body: Dict[str, Any]) -> httpx.Response:
    return await post_action(action, body)


def _explain_failure(response: httpx.Response, what: str) -> str:
    """What to tell the farmer when the adapter refused.

    A BIZ_ error is KCC's own answer -- a mobile it holds nothing for, an OTP
    it did not accept -- and is written to be read aloud. The session layer's
    two refusals mean "start again", so they are passed through as that rather
    than buried under a generic failure.
    """
    code, message = adapter_error(response)
    logger.error(
        f"KCC v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if code.startswith("BIZ_") and message:
        return message
    if "verified session" in message or "different" in message:
        return NEED_FRESH_OTP
    if code.startswith("SCH_"):
        return f"KCC {what} could not be completed. Please try again later."
    return UNAVAILABLE


def _discover_context() -> Dict[str, Any]:
    """The discover envelope, addressed to the audience KCC publishes to.

    visibleTo is matched by array overlap, and the KCC catalog is published to
    this network rather than to "*", so the query has to name the network --
    the same selector the PMFBY tool uses, and the same one the KCC folder of
    the API collection sends.
    """
    context = build_context("discover", str(uuid.uuid4()))
    context["networkId"] = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return context


async def _discover(action: str) -> Optional[Dict[str, Any]]:
    """The KCC loan resource, with its catalog provider.

    The descriptor code is what separates it: PM-KISAN, PMFBY and AIF all
    publish openagrinet:SchemeApplication, so @type alone reaches the wrong
    scheme.
    """
    body = {
        "context": _discover_context(),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        f'@.descriptor.code == "{_LOAN_CODE}")'
                    ),
                }
            }
        },
    }
    response = await _post("discover", body)
    if response.status_code != 200:
        logger.error(
            f"KCC v2 discover failed: HTTP {response.status_code} {response.text[:300]}"
        )
        return None
    found = _first_resource(response.json())
    if not found:
        return None
    advertised = [entry.get("action") for entry in _actions(found)]
    if action not in advertised:
        logger.error(
            f"KCC v2 {found['resource'].get('id')} does not advertise {action}; "
            f"catalog offers {advertised}"
        )
        return None
    return found


def _otp_length(found: Dict[str, Any]) -> int:
    """How many digits the catalog says the OTP has. Asked rather than assumed,
    so a change at KCC's end arrives with the catalog and not with a release."""
    methods = (
        (found["resource"].get("resourceAttributes") or {}).get("challenge") or {}
    ).get("methods") or []
    for method in methods:
        if method.get("method") == "OTP" and method.get("length"):
            return int(method["length"])
    return 6


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------

def _mobile(value: str) -> str:
    """10-digit Indian mobile with any +91 / 91 / 0 prefix removed; "" when invalid."""
    digits = re.sub(r"[\s-]", "", to_ascii_digits(str(value or "")))
    digits = re.sub(r"^(\+?91|0)(?=\d{10}$)", "", digits)
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else ""


def _attributes(mobile: str, **extra: Any) -> Dict[str, Any]:
    return {
        "@context": scheme_application_context(),
        "@type": SCHEME_APPLICATION_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "applicant": {"mobile": mobile},
        **extra,
    }


def _remember(store: Dict[str, Tuple[Any, float]], key: str, value: Any, ttl: int) -> None:
    store[key] = (value, time.monotonic() + ttl)


def _recall(store: Dict[str, Tuple[Any, float]], key: str, empty: Any) -> Any:
    value, expires_at = store.get(key, (empty, 0.0))
    if time.monotonic() >= expires_at:
        store.pop(key, None)
        return empty
    return value


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def _rupees(value: Any) -> str:
    """Indian digit grouping (1,00,000); "" when there is no number to show."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    whole, _, fraction = f"{amount:.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        head = ",".join(re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head))
        whole = f"{head},{tail}"
    return f"₹{whole}" + ("" if fraction == "00" else f".{fraction}")


def _status_code(row: Dict[str, Any]) -> str:
    return str((row.get("status") or {}).get("code") or "").upper()


def _status_name(row: Dict[str, Any]) -> str:
    return str((row.get("status") or {}).get("name") or "")


def _bank(row: Dict[str, Any]) -> str:
    bank = row.get("bank") or {}
    return " ".join(v for v in (bank.get("name"), bank.get("branch")) if v)


def _is_rejected(row: Dict[str, Any]) -> bool:
    return _status_code(row) == "REJECTED"


def _is_approved(row: Dict[str, Any]) -> bool:
    return _status_code(row) in APPROVED_STATUSES or bool(row.get("sanctionedAmount"))


def format_application(row: Dict[str, Any]) -> str:
    """One application, in the wording agreed with the KCC team for each outcome."""
    number = row.get("applicationNo") or ""
    requested = _rupees(row.get("requestedAmount"))
    bank = _bank(row)

    if _is_rejected(row):
        # No bank on a rejection that happened before a branch was assigned.
        sentence = f"Your loan application {number}"
        if requested:
            sentence += f" for amount {requested}"
        sentence += f" was rejected by {bank or 'the bank'}"
        if row.get("rejectionReason"):
            sentence += f" due to {row['rejectionReason']}"
        sentence += "."
        # KCC sends a rejected application back to DRAFT, which is why the
        # status name and the status code disagree here and nowhere else.
        if _status_name(row).upper() == "DRAFT":
            sentence += (
                " It is back in the Drafts section of the Krishika app,"
                " where it can be corrected and resubmitted."
            )
    elif _is_approved(row):
        sentence = f"Your loan application {number} has been approved"
        if bank:
            sentence += f" by {bank}"
        sentence += "."
        if requested:
            sentence += f" Your requested amount was {requested}."
        sanctioned = _rupees(row.get("sanctionedAmount"))
        if sanctioned:
            sentence += f" The loan has been sanctioned for {sanctioned}."
    else:
        sentence = f"Your loan application {number}"
        if requested:
            sentence += f" for amount {requested}"
        sentence += f" is currently {_status_code(row) or 'under process'}"
        if bank:
            sentence += f" with {bank}"
        sentence += "."

    lines = [SOURCE_LINE, "", sentence]
    if row.get("updatedOn"):
        lines += ["", f"Last updated: {row['updatedOn']}"]
    return "\n".join(lines)


def format_application_list(rows: List[Dict[str, Any]]) -> str:
    lines = [
        SOURCE_LINE,
        "",
        f"{len(rows)} KCC applications found for this mobile number:",
    ]
    for row in rows:
        detail = ", ".join(
            v for v in (_status_code(row), _rupees(row.get("requestedAmount"))) if v
        )
        lines.append(
            f"- {row.get('applicationNo') or ''}" + (f" ({detail})" if detail else "")
        )
    lines += [
        "",
        "Share these application numbers and ask the farmer which one to check. "
        "Then call select_kcc_application with that number — no new OTP is needed.",
    ]
    return "\n".join(lines)


def format_applications(rows: Optional[List[Dict[str, Any]]]) -> str:
    """`applications` is always an array, empty included: KCC looked and found
    none is an answer a farmer can act on, and a missing key is not."""
    if rows is None:
        return UNAVAILABLE
    if not rows:
        return f"{SOURCE_LINE}\n\nNo KCC application was found for this mobile number."
    if len(rows) == 1:
        return format_application(rows[0])
    return format_application_list(rows)


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:initiate_kcc_otp", as_type="tool")
async def initiate_kcc_otp(ctx: RunContext[FarmerContext], mobile_number: str) -> str:
    """Send an OTP to the farmer's mobile to check their Kisan Credit Card (KCC) application status.

    First step of the KCC application status check.

    Args:
        mobile_number (str): The farmer's 10-digit mobile number used for the KCC application.

    Returns:
        str: Confirmation that the OTP was sent, or the reason it failed.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return NEED_MOBILE

    try:
        transaction_id = generate_transaction_id(ctx.deps.session_id, mobile)
        lf_update_current_observation(
            metadata={
                "tool": "kcc.init",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        # A new OTP replaces the one the held requestId belongs to, and the
        # applications it fetched are about to be fetched again.
        _request_ids.pop(transaction_id, None)
        _applications.pop(transaction_id, None)

        found = await _discover("init")
        if not found:
            logger.warning("KCC v2 discover found no loan catalog")
            return UNAVAILABLE

        response = await _post(
            "init",
            commitment_envelope(
                "init", transaction_id, DRAFT, _attributes(mobile), _LOAN_OFFER, found
            ),
        )
        if response.status_code != 200:
            return _explain_failure(response, "OTP request")

        attributes = commitment_attributes(response)
        request_id = str(attributes.get("requestId") or "")
        if not request_id:
            logger.error("KCC v2 init returned no requestId")
            return UNAVAILABLE
        _remember(_request_ids, transaction_id, request_id, _REQUEST_ID_TTL_SECONDS)

        issued = (attributes.get("challenge") or {}).get("issued") or {}
        length = issued.get("length") or _otp_length(found)
        lines = [
            "An OTP has been sent to the mobile number registered with KCC. "
            f"Please share the {length}-digit OTP to see the application status."
        ]
        if issued.get("sentTo"):
            lines.append(f"Sent to mobile: {issued['sentTo']}")
        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error("KCC v2 init timed out")
        return "The KCC OTP request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"KCC v2 init failed: {e}")
        return UNAVAILABLE
    except UnexpectedModelBehavior:
        logger.warning("KCC v2 init exceeded retry limit")
        return UNAVAILABLE
    except Exception as e:
        logger.error(f"Error in KCC v2 init: {e}")
        raise ModelRetry(f"Unexpected error in KCC OTP request. {str(e)}")


@observe(name="tool:check_kcc_application_status", as_type="tool")
async def check_kcc_application_status(
    ctx: RunContext[FarmerContext], mobile_number: str, otp: str
) -> str:
    """Check the farmer's Kisan Credit Card (KCC) application status. Call after initiate_kcc_otp.

    Submits the OTP; KCC verifies it and returns the applications in the same call.

    Args:
        mobile_number (str): The same mobile number used for initiate_kcc_otp.
        otp (str): The 6-digit OTP the farmer received by SMS.

    Returns:
        str: The application status and details, or the reason it failed.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return NEED_MOBILE

    otp_clean = numeric(otp)
    if len(otp_clean) != 6:
        return NEED_OTP

    try:
        transaction_id = generate_transaction_id(ctx.deps.session_id, mobile)
        lf_update_current_observation(
            metadata={
                "tool": "kcc.status",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        request_id = _recall(_request_ids, transaction_id, "")
        if not request_id:
            return NEED_FRESH_OTP

        found = await _discover("status")
        if not found:
            logger.warning("KCC v2 discover found no loan catalog")
            return UNAVAILABLE

        response = await _post(
            "status",
            commitment_envelope(
                "status",
                transaction_id,
                ACTIVE,
                _attributes(
                    mobile,
                    requestId=request_id,
                    challenge={"response": {"method": "OTP", "value": otp_clean}},
                ),
                _LOAN_OFFER,
                found,
            ),
        )
        if response.status_code != 200:
            return _explain_failure(response, "application status check")

        rows = commitment_attributes(response).get("applications")
        if isinstance(rows, list):
            # Held so that choosing between several does not spend a second
            # OTP: KCC has no per-application endpoint, and the list it just
            # sent already holds every row select could ask for.
            _remember(_applications, transaction_id, rows, _APPLICATIONS_TTL_SECONDS)
        return format_applications(rows)

    except httpx.TimeoutException:
        logger.error("KCC v2 status timed out")
        return "The KCC status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"KCC v2 status failed: {e}")
        return UNAVAILABLE
    except UnexpectedModelBehavior:
        logger.warning("KCC v2 status exceeded retry limit")
        return UNAVAILABLE
    except Exception as e:
        logger.error(f"Error in KCC v2 status: {e}")
        raise ModelRetry(f"Unexpected error in KCC status request. {str(e)}")


@observe(name="tool:select_kcc_application", as_type="tool")
async def select_kcc_application(
    ctx: RunContext[FarmerContext], mobile_number: str, application_no: str
) -> str:
    """Show one KCC application after check_kcc_application_status listed several.

    Uses the applications already fetched with the OTP; no new OTP is needed.

    Args:
        mobile_number (str): The same mobile number used for initiate_kcc_otp.
        application_no (str): The application number the farmer chose from the list.

    Returns:
        str: That application's status, or the reason it failed.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return NEED_MOBILE

    wanted = numeric(application_no)
    if not wanted:
        return NEED_APPLICATION_NO

    transaction_id = generate_transaction_id(ctx.deps.session_id, mobile)
    lf_update_current_observation(
        metadata={
            "tool": "kcc.select",
            "transaction_id": transaction_id,
            "beckn_version": "v2",
        }
    )
    # Nothing is sent: status already returned every application on this
    # mobile, so this is a choice among what is in hand rather than a question
    # KCC can be asked again without a new OTP.
    rows = _recall(_applications, transaction_id, [])
    if not rows:
        return NEED_FRESH_OTP

    for row in rows:
        if numeric(row.get("applicationNo")) == wanted:
            return format_application(row)

    numbers = ", ".join(str(row.get("applicationNo") or "") for row in rows)
    return (
        f"No KCC application numbered {wanted} was in the list for this mobile number. "
        f"The applications on record are: {numbers}."
    )
