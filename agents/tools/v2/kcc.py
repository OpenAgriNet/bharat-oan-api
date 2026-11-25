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

**Nothing about KCC's request is written here.** discover answers with the
catalog, every action in it carries an operationRef, and that document names the
fields, their types, which are required, and where each value comes from. This
module holds three values -- a mobile number, an OTP, and the session init
handed over -- and places them in whichever fields the contract says they
belong in. A field KCC adds arrives with the spec; a field this module cannot
fill stops the call and says which one, rather than sending a short body and
letting KCC refuse it.

**The session is held by the adapter, not here.** What this module holds is the
token KCC wants inside the body of the status call, which the adapter cannot put
into a body it did not build. Holding it is also what lets a farmer answer the
OTP once and then browse their applications.

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
    pack_attributes,
    post_action,
    scheme_application_context,
    SCHEME_APPLICATION_TYPE,
)
from agents.tools.v2.provider_openapi import (
    FieldSpec,
    From,
    SchemaUnavailable,
    VALUE_SOURCE,
    filled_fields,
    request_fields,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

SOURCE_LINE = "**Source:** Kisan Rin Portal"

# The catalog publishes one resource under this code. It is the question being
# asked -- PM-KISAN, PMFBY and AIF all publish openagrinet:SchemeApplication,
# so @type alone reaches the wrong scheme -- and everything else about the
# capability comes off the discover answer.
_LOAN_CODE = "kcc-loan"

UNAVAILABLE = "The KCC system cannot be reached right now. Please try again in a few minutes."
NEED_MOBILE = "Ask the farmer for the 10-digit mobile number they used for their KCC application."
NEED_APPLICATION_NO = "Ask the farmer which KCC application number to check (digits only)."
NEED_FRESH_OTP = (
    "This KCC check needs a fresh OTP. Send a new one and ask the farmer to read it out."
)


def need_otp(length: int) -> str:
    return f"Ask the farmer for the {length}-digit OTP sent to their mobile."


# KCC's session is good for five minutes (sessionTtl on the binding), so one
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
    return await post_action(action, body, who="KCC")


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
    """The KCC loan resource, with its catalog provider and offer."""
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
    return _first_resource(
        response.json(),
        usable=lambda candidate: _unusable(candidate, action),
        who="KCC",
    )


def _unusable(found: Dict[str, Any], action: str) -> Optional[str]:
    """Why this resource cannot serve the action, or None when it can. Asked of
    every candidate rather than the first, so a second provider publishing
    kcc-loan is tried instead of making the whole discover fail."""
    advertised = [entry.get("action") for entry in _actions(found)]
    if action not in advertised:
        return f"does not advertise {action}; catalog offers {advertised}"
    if not (found.get("offer") or {}).get("id"):
        return "is in no published offer; a commitment has to be made against one"
    return None


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
# The provider's own request fields
# --------------------------------------------------------------------------------------

async def _request_attributes(found: Dict[str, Any], action: str) -> Dict[str, FieldSpec]:
    """What this action's call has to carry, read from the contract the catalog names."""
    return await request_fields(found["resource"], action)


async def _session_field(found: Dict[str, Any]) -> str:
    """The name KCC gives the token init hands over and status quotes back.

    Read off the status contract rather than the init one: the field belongs to
    the call that spends it, and both actions are in the one document the
    catalog points at, so asking costs nothing extra.
    """
    for name, spec in (await _request_attributes(found, "status")).items():
        if spec.hint == From.SESSION:
            return name
    raise SchemaUnavailable(
        f"{found['resource'].get('id')} status declares no {VALUE_SOURCE}: {From.SESSION} field"
    )


def _attributes(found: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """The commitment attributes for a KCC call. The same pack shape AIF builds,
    so one change of mind about the envelope is one change of code."""
    return pack_attributes(
        found,
        fields,
        scheme_application_context(),
        SCHEME_APPLICATION_TYPE,
        informationMode=None,
        scheme=None,
    )


def _remember(store: Dict[str, Tuple[Any, float]], key: str, value: Any, ttl: int) -> None:
    store[key] = (value, time.monotonic() + ttl)


def _recall(store: Dict[str, Tuple[Any, float]], key: str, empty: Any) -> Any:
    value, expires_at = store.get(key, (empty, 0.0))
    if time.monotonic() >= expires_at:
        store.pop(key, None)
        return empty
    return value


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------

def _mobile(value: str) -> str:
    """10-digit Indian mobile with any +91 / 91 / 0 prefix removed; "" when invalid."""
    digits = re.sub(r"[\s-]", "", to_ascii_digits(str(value or "")))
    digits = re.sub(r"^(\+?91|0)(?=\d{10}$)", "", digits)
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else ""


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
        # A new OTP replaces the one the held session belongs to, and the
        # applications it fetched are about to be fetched again.
        _request_ids.pop(transaction_id, None)
        _applications.pop(transaction_id, None)

        found = await _discover("init")
        if not found:
            logger.warning("KCC v2 discover found no loan catalog")
            return UNAVAILABLE

        fields, unfillable = filled_fields(
            await _request_attributes(found, "init"), {From.MOBILE: mobile}
        )
        if unfillable:
            logger.error(f"KCC v2 cannot fill the init contract: {unfillable}")
            return UNAVAILABLE
        session_field = await _session_field(found)

        response = await _post(
            "init",
            commitment_envelope(
                "init",
                transaction_id,
                DRAFT,
                _attributes(found, fields),
                found["offer"]["id"],
                found,
            ),
        )
        if response.status_code != 200:
            return _explain_failure(response, "OTP request")

        attributes = commitment_attributes(response)
        request_id = str(attributes.get(session_field) or "")
        if not request_id:
            logger.error(f"KCC v2 init returned no {session_field}")
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

    except SchemaUnavailable as e:
        logger.error(f"KCC v2 init could not read the request contract: {e}")
        return UNAVAILABLE
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

        # Checked against the catalog rather than a constant, so a farmer is
        # asked to re-read a code of the length KCC is actually sending.
        length = _otp_length(found)
        if len(otp_clean) != length:
            return need_otp(length)

        fields, unfillable = filled_fields(
            await _request_attributes(found, "status"),
            {From.MOBILE: mobile, From.OTP: otp_clean, From.SESSION: request_id},
        )
        if unfillable:
            logger.error(f"KCC v2 cannot fill the status contract: {unfillable}")
            return UNAVAILABLE

        response = await _post(
            "status",
            commitment_envelope(
                "status",
                transaction_id,
                ACTIVE,
                _attributes(found, fields),
                found["offer"]["id"],
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

    except SchemaUnavailable as e:
        logger.error(f"KCC v2 status could not read the request contract: {e}")
        return UNAVAILABLE
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
