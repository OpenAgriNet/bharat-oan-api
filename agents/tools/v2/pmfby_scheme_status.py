"""
PMFBY policy and claim status over Beckn v2.

Same two tools, same arguments, same purpose as agents.tools.pmfby_scheme_status
-- only the transport changes. v1 posted /init and /status at the BAP and read
the answer out of tag soup; v2 posts four actions at the consumer adapter
against one SchemeApplication/v0.2 capability:

    init     -> PMFBY sends a six-digit OTP
    confirm  -> the OTP is spent, and PMFBY returns the farmerId both reads need
    status   -> the farmer's POLICIES
    select   -> the farmer's CLAIMS

Both reads are the same capability, so the action is what tells a policy
question from a claim question -- which is why inquiry_type picks the action
rather than a field.

**The session is held by the adapter, not here.** confirm buys a session the
adapter keeps against the transaction id and binds to the mobile number it was
bought for. What this module does hold is the farmerId: it is a query
parameter on both reads, and the adapter cannot put a held value into a query
string. Holding it is why a farmer who asks for policies and then for claims
spends ONE OTP -- PMFBY refuses a second verify with the same code.

The output is NOT byte-comparable with v1. v1 rendered whatever tags the
gateway returned; v2 reads named fields out of the v0.2 pack. See
migration-plan/pmfby/plan.md.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.pmfby_grievance import _normalize_request_season_for_pmfby_api
from agents.tools.pmfby_scheme_status import (
    generate_transaction_id,
    normalize_phone_for_api,
)
from agents.tools.v2.aif import (
    ACTIVE,
    DRAFT,
    SCHEME_APPLICATION_TYPE,
    _actions,
    _first_resource,
    adapter_error,
    adapter_url,
    build_context,
    commitment_attributes,
    commitment_envelope,
    scheme_application_context,
)
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

SCHEME = {"code": "PMFBY"}
SOURCE_LINE = "**Source:** PMFBY Portal"

# discover returns catalogs and resources, never offers, so this stays a
# constant; the provider and the resource id come off the wire.
_INSURANCE_OFFER = "off:pmfby:insurance"

# PMFBY's own numbering, which is what select sends. The names are the ones
# the farmer and the v1 signature use.
_SEASON_NAMES = {"1": "Kharif", "2": "Rabi", "3": "Summer"}

# The adapter holds the session for 15 minutes (sessionTtl on the binding), so
# a farmerId older than that cannot be spent anyway.
_FARMER_ID_TTL_SECONDS = 15 * 60
_farmer_ids: Dict[str, tuple] = {}

_UNAVAILABLE_MSG = (
    "The PMFBY portal is not available right now. Please try again later."
)

NEED_MOBILE = "Ask the farmer for the 10-digit mobile number registered with PMFBY."
NEED_OTP = "Ask the farmer for the 6-digit OTP sent to their registered mobile."
NEED_YEAR = "Ask the farmer which crop year they mean, as four digits (e.g. 2023)."
NEED_SEASON = "Ask the farmer which season they mean: Kharif, Rabi or Summer."


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

    A BIZ_ error is PMFBY's own answer and is written to be read aloud. The two
    the session layer raises -- an expired session and a question about another
    mobile number -- mean "verify again", so they are passed through as that
    rather than buried.
    """
    code, message = adapter_error(response)
    logger.error(
        f"PMFBY v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if "verified session" in message or "different" in message:
        return (
            "This PMFBY check needs a fresh verification. "
            "Send a new OTP and ask the farmer to read it out."
        )
    if code.startswith("BIZ_") and message:
        return message
    if code.startswith("SCH_"):
        return f"PMFBY {what} could not be completed. Please try again later."
    return f"PMFBY {what} is temporarily unavailable. Please try again later."


def _discover_context() -> Dict[str, Any]:
    """The discover envelope, addressed to the audience PMFBY publishes to.

    visibleTo is matched by array overlap, and the PMFBY catalog is published
    to this network rather than to "*", so the query has to name the network --
    the same selector the PM-KISAN tool uses, and the same one the PMFBY folder
    of the API collection sends.
    """
    context = build_context("discover", str(uuid.uuid4()))
    context["networkId"] = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return context


async def _discover(action: str) -> Optional[Dict[str, Any]]:
    """The PMFBY insurance resource, with its catalog provider.

    The scheme code is not a nicety: PM-KISAN publishes
    openagrinet:SchemeApplication too, so matching on @type alone reaches the
    wrong scheme.
    """
    body = {
        "context": _discover_context(),
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
    response = await _post("discover", body)
    if response.status_code != 200:
        logger.error(
            f"PMFBY v2 discover failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
        return None
    found = _first_resource(response.json())
    if not found:
        return None
    advertised = [entry.get("action") for entry in _actions(found)]
    if action not in advertised:
        logger.error(
            f"PMFBY v2 {found['resource'].get('id')} does not advertise {action}; "
            f"catalog offers {advertised}"
        )
        return None
    return found


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------

def _mobile(phone_number: str) -> str:
    """The 10-digit number PMFBY registers against, or "" to ask again."""
    digits = normalize_phone_for_api(phone_number or "")
    return digits if len(digits) == 10 and digits.isdigit() else ""


def _otp(value: str) -> str:
    """The six digits, or "" when it is not six. PMFBY refuses anything else,
    so it is caught before the OTP is spent rather than after."""
    digits = "".join(c for c in to_ascii_digits(str(value or "")) if c.isdigit())
    return digits if len(digits) == 6 else ""


def _crop_year(value: str) -> str:
    digits = to_ascii_digits(str(value or "")).strip()
    return digits if len(digits) == 4 and digits.isdigit() else ""


def _season_code(value: str) -> str:
    """Kharif/Rabi/Summer as PMFBY's 1/2/3, or "" to ask again."""
    code = _normalize_request_season_for_pmfby_api(value or "")
    return code if code in _SEASON_NAMES else ""


def _attributes(mobile: str, **extra: Any) -> Dict[str, Any]:
    return {
        "@context": scheme_application_context(),
        "@type": SCHEME_APPLICATION_TYPE,
        "informationMode": "OnDemand",
        "scheme": dict(SCHEME),
        "applicant": {"mobile": mobile},
        **extra,
    }


def _remember_farmer_id(transaction_id: str, farmer_id: str) -> None:
    _farmer_ids[transaction_id] = (farmer_id, time.monotonic() + _FARMER_ID_TTL_SECONDS)


def _recall_farmer_id(transaction_id: str) -> str:
    farmer_id, expires_at = _farmer_ids.get(transaction_id, ("", 0.0))
    if not farmer_id or time.monotonic() >= expires_at:
        _farmer_ids.pop(transaction_id, None)
        return ""
    return farmer_id


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def _amount(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"₹{value:,.0f}"
    return ""


def _matches(row: Dict[str, Any], season: str, year: str) -> bool:
    """PMFBY keys its policy list by series, not by season, so the season and
    year the farmer asked for are matched here rather than sent."""
    row_season = str(row.get("season") or "").strip().lower()
    row_year = str(row.get("cropYear") or "").strip()
    return row_season == season.lower() and row_year == year


def _policy_lines(row: Dict[str, Any]) -> List[str]:
    crop = row.get("crop") or "Crop"
    status = (row.get("status") or {}).get("code") or "Status not stated"
    lines = [f"{crop} — {status}"]
    if row.get("applicationNo"):
        lines.append(f"  Application {row['applicationNo']}")
    insured = _amount(row.get("sumInsured"))
    premium = _amount(row.get("premiumFarmerShare"))
    if insured:
        lines.append(f"  Sum insured {insured}")
    if premium:
        lines.append(f"  Farmer's premium {premium}")
    if row.get("insurer"):
        lines.append(f"  Insurer {row['insurer']}")
    claim = row.get("claim") or {}
    if claim.get("status"):
        amount = _amount(claim.get("amount"))
        lines.append(f"  Claim: {claim['status']}" + (f" ({amount})" if amount else ""))
    return lines


def format_policies(
    attributes: Dict[str, Any], season: str, year: str
) -> str:
    """`policies` is always an array, empty included: PMFBY looked and found
    none is an answer a farmer can act on, and a missing key is not."""
    policies = attributes.get("policies")
    if policies is None:
        return "PMFBY could not list the policies right now. Please try again later."
    if not policies:
        return f"{SOURCE_LINE}\n\nPMFBY has no insurance policy on record for this farmer."

    wanted = [row for row in policies if _matches(row, season, year)]
    lines = [SOURCE_LINE, ""]
    if wanted:
        lines.append(f"{len(wanted)} policy entry(ies) for {season} {year}.")
        lines.append("")
        for row in wanted:
            lines.extend(_policy_lines(row))
            lines.append("")
        return "\n".join(lines).strip()

    # Not an error: PMFBY answered, just not for the season asked about. What
    # it did send is named so the farmer is not told "nothing" twice.
    seasons = sorted({
        f"{row.get('season') or '?'} {row.get('cropYear') or '?'}" for row in policies
    })
    lines.append(f"No PMFBY policy found for {season} {year}.")
    lines.append(f"This farmer does have policies for: {', '.join(seasons)}.")
    return "\n".join(lines)


def _claim_lines(row: Dict[str, Any]) -> List[str]:
    crop = (row.get("crop") or {}).get("name") or "Crop"
    status = (row.get("status") or {}).get("code") or "Status not stated"
    lines = [f"{crop} — {status}"]
    if row.get("applicationNo"):
        lines.append(f"  Application {row['applicationNo']}")
    if row.get("type"):
        lines.append(f"  Claim type {row['type']}")
    for label, key in (("Claimed", "amountClaimed"), ("Payable", "amountPayable"), ("Paid", "amountPaid")):
        amount = _amount(row.get(key))
        if amount:
            lines.append(f"  {label} {amount}")
    payment = row.get("payment") or {}
    if payment.get("status"):
        lines.append(f"  Payment: {payment['status']}")
    if row.get("settledOn"):
        lines.append(f"  Settled on {row['settledOn']}")
    return lines


def format_claims(attributes: Dict[str, Any], season: str, year: str) -> str:
    """`claims` is always an array, empty included, for the same reason."""
    claims = attributes.get("claims")
    if claims is None:
        return "PMFBY could not list the claims right now. Please try again later."
    if not claims:
        return f"{SOURCE_LINE}\n\nPMFBY has no crop-loss claim on record for {season} {year}."

    lines = [SOURCE_LINE, "", f"{len(claims)} claim(s) for {season} {year}.", ""]
    for row in claims:
        lines.extend(_claim_lines(row))
        lines.append("")
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:initiate_pmfby_status_check", as_type="tool")
async def initiate_pmfby_status_check(
    ctx: RunContext[FarmerContext], phone_number: str
) -> str:
    """Initiate PMFBY status check by sending OTP to farmer's mobile.

    Use this tool to initiate the process. Sends an OTP to the farmer's registered mobile.
    After OTP is received, use check_pmfby_status_with_otp with the OTP and inquiry details.

    Args:
        phone_number (str): Phone number registered with PMFBY (required)

    Returns:
        str: Response from the scheme init service (OTP sent confirmation)
    """
    mobile = _mobile(phone_number)
    if not mobile:
        return NEED_MOBILE

    try:
        transaction_id = generate_transaction_id(ctx.deps.session_id, phone_number)
        lf_update_current_observation(
            metadata={
                "tool": "pmfby.init",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        # A new OTP replaces the one the old farmerId was bought with, so the
        # held farmerId goes with it rather than outliving its session.
        _farmer_ids.pop(transaction_id, None)

        found = await _discover("init")
        if not found:
            logger.warning("PMFBY v2 discover found no insurance catalog")
            return _UNAVAILABLE_MSG
        body = commitment_envelope(
            "init",
            transaction_id,
            DRAFT,
            _attributes(mobile),
            _INSURANCE_OFFER,
            found,
        )
        response = await _post("init", body)
        if response.status_code != 200:
            return _explain_failure(response, "OTP request")

        issued = (commitment_attributes(response).get("challenge") or {}).get("issued") or {}
        length = issued.get("length") or 6
        lines = [
            "An OTP has been sent to the mobile number registered with PMFBY. "
            f"Please share the {length}-digit OTP to see the policy or claim status."
        ]
        if issued.get("sentTo"):
            lines.append(f"Registered mobile: {issued['sentTo']}")
        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error("PMFBY v2 init timed out")
        return "PMFBY init request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PMFBY v2 init failed: {e}")
        return f"PMFBY init request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("PMFBY v2 init exceeded retry limit")
        return "The PMFBY system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in PMFBY v2 init: {e}")
        raise ModelRetry(f"Unexpected error in PMFBY init request. {str(e)}")


async def _verified_farmer_id(
    transaction_id: str, mobile: str, otp: str, found: Dict[str, Any]
) -> tuple:
    """(farmerId, failure message). The OTP is spent at most once per OTP:
    a second read in the same conversation reuses the farmerId confirm bought,
    because PMFBY refuses a second verify with the same code."""
    held = _recall_farmer_id(transaction_id)
    if held:
        return held, ""

    body = commitment_envelope(
        "confirm",
        transaction_id,
        DRAFT,
        _attributes(
            mobile,
            challenge={"response": {"method": "OTP", "value": otp}},
        ),
        _INSURANCE_OFFER,
        found,
    )
    response = await _post("confirm", body)
    if response.status_code != 200:
        return "", _explain_failure(response, "OTP verification")

    farmer_id = str(commitment_attributes(response).get("farmerId") or "")
    if not farmer_id:
        logger.error("PMFBY v2 confirm returned no farmerId")
        return "", _UNAVAILABLE_MSG
    _remember_farmer_id(transaction_id, farmer_id)
    return farmer_id, ""


@observe(name="tool:check_pmfby_status_with_otp", as_type="tool")
async def check_pmfby_status_with_otp(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    inquiry_type: Literal["policy_status", "claim_status"],
    year: str,
    season: Literal["Kharif", "Rabi", "Summer"],
) -> str:
    """Check PMFBY status using OTP after initiating the OTP check.

    Use this tool to check policy or claim status using the OTP received via SMS
    after calling initiate_pmfby_status_check.

    Args:
        otp (str): 6-digit OTP received via SMS
        phone_number (str): Phone number registered with PMFBY (required)
        inquiry_type (str): Type of inquiry - 'policy_status' or 'claim_status' (required)
        year (str): Year for which status is requested (required)
        season (str): Season - Kharif, Rabi, or Summer (required)

    Returns:
        str: Detailed scheme status information
    """
    mobile = _mobile(phone_number)
    if not mobile:
        return NEED_MOBILE

    otp_clean = _otp(otp)
    if not otp_clean:
        return NEED_OTP

    crop_year = _crop_year(year)
    if not crop_year:
        return NEED_YEAR

    season_code = _season_code(season)
    if not season_code:
        return NEED_SEASON
    season_name = _SEASON_NAMES[season_code]

    # One capability, two reads: policies are status and claims are select.
    action = "status" if inquiry_type == "policy_status" else "select"

    try:
        transaction_id = generate_transaction_id(ctx.deps.session_id, phone_number)
        lf_update_current_observation(
            metadata={
                "tool": f"pmfby.{action}",
                "transaction_id": transaction_id,
                "beckn_version": "v2",
            }
        )
        found = await _discover(action)
        if not found:
            logger.warning("PMFBY v2 discover found no insurance catalog")
            return _UNAVAILABLE_MSG

        farmer_id, failure = await _verified_farmer_id(
            transaction_id, mobile, otp_clean, found
        )
        if not farmer_id:
            return failure

        body = commitment_envelope(
            action,
            transaction_id,
            ACTIVE,
            _attributes(
                mobile, farmerId=farmer_id, season=season_code, year=crop_year
            ),
            _INSURANCE_OFFER,
            found,
        )
        response = await _post(action, body)
        if response.status_code != 200:
            # The session the farmerId belongs to is gone or was refused, so
            # the farmerId is dropped with it and the next attempt re-verifies.
            _farmer_ids.pop(transaction_id, None)
            what = "policy status check" if action == "status" else "claim status check"
            return _explain_failure(response, what)

        attributes = commitment_attributes(response)
        if action == "status":
            return format_policies(attributes, season_name, crop_year)
        return format_claims(attributes, season_name, crop_year)

    except httpx.TimeoutException:
        logger.error(f"PMFBY v2 {action} timed out")
        return "PMFBY status request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"PMFBY v2 {action} failed: {e}")
        return f"PMFBY status request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning(f"PMFBY v2 {action} exceeded retry limit")
        return "The PMFBY system is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in PMFBY v2 {action}: {e}")
        raise ModelRetry(f"Unexpected error in PMFBY status request. {str(e)}")
