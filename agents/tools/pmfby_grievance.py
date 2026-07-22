"""
PMFBY Grievance Tool for lodging a grievance through the PMFBY scheme.

Implements the Beckn BAP `/init` flow for provider `pmfby-grievance` using the same
transaction-id + OTP validation conventions as the PMFBY scheme status tools.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.utils import get_logger, to_ascii_digits
from langfuse import observe
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext

logger = get_logger(__name__)

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

TICKET_CATEGORY_ID = "3"
TICKET_SUB_CATEGORY_ID = "10"
PMFBY_GRIEVANCE_RECEIPT_SOURCE_ID = "134306"

_OTP_FAILURE_SUBSTRINGS = (
    "invalid otp",
    "otp invalid",
    "wrong otp",
    "expired otp",
    "otp expired",
    "verification failed",
    "otp mismatch",
    "incorrect otp",
)

_OTP_ERROR_DESCRIPTOR_CODES = frozenset(
    {"invalid_otp", "farmer_id_not_found", "error", "not_found"}
)


def _today_yyyy_mm_dd() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def generate_transaction_id(session_id: str, key: str) -> str:
    """Deterministic transaction id across OTP init, status verify, and grievance submit."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, (session_id + key)))


def normalize_phone_for_api(phone: str) -> str:
    """Digits only; 10-digit Indian mobile for BAP (no country code)."""
    digits = "".join(c for c in to_ascii_digits(phone).strip() if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits if digits else phone.strip()


def _validate_registered_mobile(phone_number: str) -> str:
    """Require a 10-digit PMFBY-registered mobile; reject OTP-length input."""
    phone = normalize_phone_for_api(
        _require_nonempty(phone_number, "Please share your registered mobile number.")
    )
    if not phone.isdigit():
        raise ModelRetry("Please share a valid **10-digit** mobile number registered with PMFBY.")
    if len(phone) == 6:
        raise ModelRetry(
            "The farmer shared a 6-digit number — that is OTP length, not a mobile number. "
            "If OTP was not sent yet, ask for their **10-digit registered mobile** and call "
            "`initiate_pmfby_grievance_otp(phone_number)`. Do not ask for grievance details yet."
        )
    if len(phone) != 10:
        raise ModelRetry("Please share a valid **10-digit** mobile number registered with PMFBY.")
    return phone


def _validate_otp(otp: str) -> str:
    otp_str = to_ascii_digits(otp).strip() if otp else ""
    if not otp_str:
        raise ModelRetry("Invalid OTP. Please provide the 6-digit OTP received via SMS.")
    digits = "".join(c for c in otp_str if c.isdigit())
    if len(digits) != 6:
        raise ModelRetry("PMFBY OTP must be exactly 6 digits. Please ask for the 6-digit OTP received on mobile.")
    return digits


def _normalize_request_season_for_pmfby_api(season: str) -> str:
    """Map Kharif/Rabi/Zaid (and 1–3) to PMFBY-style season ids for `request_season` tag."""
    raw = str(season).strip()
    if not raw:
        return raw
    low = raw.lower()
    try:
        n = int(raw)
        if 1 <= n <= 3:
            return str(n)
    except ValueError:
        pass
    if "kharif" in low or "खरीफ" in raw or "monsoon" in low:
        return "1"
    if "rabi" in low or "रबी" in raw:
        return "2"
    if "summer" in low or "zaid" in low or "जायद" in raw or "ग्रीष्म" in raw:
        return "3"
    return raw


def _season_name_for_status_api(season: str) -> str:
    """Map grievance/status season input to BAP `season` tag (Kharif/Rabi/Summer)."""
    api = _normalize_request_season_for_pmfby_api(season)
    return {"1": "Kharif", "2": "Rabi", "3": "Summer"}.get(api, season.strip())


def _require_nonempty(value: Optional[str], retry_message: str) -> str:
    s = str(value).strip() if value is not None else ""
    if not s:
        raise ModelRetry(retry_message)
    return s


# --------------------------------------------------------------------------------------
# Beckn transport
# --------------------------------------------------------------------------------------


def _bap_url(path: str) -> str:
    base = os.getenv("BAP_ENDPOINT", "").rstrip("/")
    if not base:
        raise ModelRetry("BAP endpoint is not configured. Set BAP_ENDPOINT.")
    return f"{base}/{path.lstrip('/')}"


def _beckn_context(*, transaction_id: str, action: str) -> Dict[str, Any]:
    return {
        "domain": "schemes:vistaar",
        "action": action,
        "version": "1.1.0",
        "bap_id": os.getenv("BAP_ID"),
        "bap_uri": os.getenv("BAP_URI"),
        "bpp_id": os.getenv("BPP_ID"),
        "bpp_uri": os.getenv("BPP_URI"),
        "transaction_id": transaction_id,
        "message_id": str(uuid.uuid4()),
        "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
        "ttl": "PT10M",
        "location": {"country": {"code": "IND"}, "city": {"code": "*"}},
    }


def _post_json_logged(url: str, payload: Dict[str, Any], log_prefix: str) -> httpx.Response:
    logger.info("%s URL: %s", log_prefix, url)
    logger.info("%s Payload: %s", log_prefix, json.dumps(payload, indent=2))
    resp = httpx.post(url, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)
    logger.info("%s Status: %s", log_prefix, resp.status_code)
    logger.info("%s Body: %s", log_prefix, resp.text)
    return resp


# --------------------------------------------------------------------------------------
# Request payloads
# --------------------------------------------------------------------------------------


def _payload_get_otp_grievance_flow(*, transaction_id: str, phone: str) -> Dict[str, Any]:
    return {
        "context": _beckn_context(transaction_id=transaction_id, action="init"),
        "message": {
            "order": {
                "provider": {"id": "pmfby-agri"},
                "items": [{"id": "pmfby"}],
                "fulfillments": [
                    {
                        "customer": {
                            "person": {
                                "tags": [
                                    {"descriptor": {"code": "request_type"}, "value": "get_otp"},
                                    {"descriptor": {"code": "phone_number"}, "value": phone},
                                ]
                            },
                            "contact": {"phone": phone},
                        }
                    }
                ],
            }
        },
    }


def _payload_grievance_status(
    *, transaction_id: str, phone: str, grievance_support_ticket_no: str
) -> Dict[str, Any]:
    """Beckn `/search` for PMFBY grievance ticket lookup (`status_grievance`)."""
    ticket = str(grievance_support_ticket_no).strip()
    return {
        "context": _beckn_context(transaction_id=transaction_id, action="search"),
        "message": {
            "order": {
                "provider": {"id": "pmfby-grievance"},
                "items": [{"id": "pmfby-grievance"}],
                "fulfillments": [
                    {
                        "customer": {
                            "person": {
                                "tags": [
                                    {
                                        "descriptor": {"code": "request_type"},
                                        "value": "status_grievance",
                                    },
                                    {
                                        "descriptor": {"code": "requestorMobileNo"},
                                        "value": phone,
                                    },
                                    {
                                        # API field name (typo preserved for BAP compatibility).
                                        "descriptor": {"code": "GrievenceSupportTicketNo"},
                                        "value": ticket,
                                    },
                                ]
                            }
                        }
                    }
                ],
            },
        },
    }


def _payload_status_verify_otp(
    *,
    transaction_id: str,
    otp: str,
    phone: str,
    inquiry_type: str,
    year: str,
    season: str,
) -> Dict[str, Any]:
    return {
        "context": _beckn_context(transaction_id=transaction_id, action="status"),
        "message": {
            "order_id": otp,
            "order": {
                "id": "order-1",
                "provider": {"id": "pmfby-agri"},
                "items": [{"id": "pmfby"}],
                "fulfillments": [
                    {
                        "customer": {
                            "person": {
                                "tags": [
                                    {"descriptor": {"code": "inquiry_type"}, "value": inquiry_type},
                                    {"descriptor": {"code": "year"}, "value": year},
                                    {"descriptor": {"code": "season"}, "value": season},
                                ]
                            },
                            "contact": {"phone": phone},
                        }
                    }
                ],
            },
        },
    }


def _iter_order_tags(order: Dict[str, Any]):
    for tag in order.get("tags") or []:
        yield tag
    for item in order.get("items") or []:
        for tag in item.get("tags") or []:
            yield tag


def _bap_status_error_message(res_json: Dict[str, Any]) -> Optional[str]:
    """Extract user-facing error text from BAP on_status (e.g. invalid OTP)."""
    parts: List[str] = []
    for block in res_json.get("responses") or []:
        order = (block.get("message") or {}).get("order") or {}
        if (order.get("id") or "").strip().lower() == "error":
            for tag in _iter_order_tags(order):
                d = tag.get("descriptor") or {}
                short_desc = (d.get("short_desc") or "").strip()
                if short_desc:
                    parts.append(short_desc)
                    continue
                name = (d.get("name") or "").strip()
                code = (d.get("code") or "").strip()
                if name and name.lower() != "error":
                    parts.append(name)
                elif code:
                    parts.append(code.replace("_", " "))
            continue
        for tag in _iter_order_tags(order):
            d = tag.get("descriptor") or {}
            code = (d.get("code") or "").lower()
            if code in _OTP_ERROR_DESCRIPTOR_CODES:
                short_desc = (d.get("short_desc") or "").strip()
                parts.append(short_desc or code.replace("_", " "))
            for item in tag.get("list") or []:
                d2 = item.get("descriptor") or {}
                code2 = (d2.get("code") or "").lower()
                if code2 in _OTP_ERROR_DESCRIPTOR_CODES:
                    val = item.get("value")
                    parts.append(str(val) if val else code2.replace("_", " "))
    unique = list(dict.fromkeys(part for part in parts if part))
    return "; ".join(unique) if unique else None


def _has_otp_verified_signal(order: Dict[str, Any]) -> bool:
    for tag in _iter_order_tags(order):
        if tag.get("list"):
            for item in tag["list"]:
                code = ((item.get("descriptor") or {}).get("code") or "").lower()
                val = str(item.get("value") or "").lower()
                if code == "otp_verified" and val in ("true", "yes", "1", "verified", "success"):
                    return True
        d = tag.get("descriptor") or {}
        if (d.get("code") or "").lower() == "otp_verified":
            val = str(tag.get("value") or "").lower()
            if val in ("true", "yes", "1", "verified", "success"):
                return True
    return False


def _has_policy_catalog_data(message: Dict[str, Any]) -> bool:
    catalog = message.get("catalog") or {}
    if isinstance(catalog.get("order"), dict):
        catalog = catalog["order"]
    for prov in catalog.get("providers") or []:
        if prov.get("items"):
            return True

    order = message.get("order") or {}
    nested_catalog = order.get("catalog") or {}
    for prov in nested_catalog.get("providers") or []:
        if prov.get("items"):
            return True
    if order.get("items"):
        return True
    for prov in order.get("providers") or []:
        if prov.get("items"):
            return True
    return False


def _pmfby_otp_status_verified(res_json: Dict[str, Any]) -> bool:
    blob = json.dumps(res_json, ensure_ascii=False).lower()
    if any(s in blob for s in _OTP_FAILURE_SUBSTRINGS):
        return False
    if "invalid_otp" in blob:
        return False

    for block in res_json.get("responses") or []:
        message = block.get("message") or {}
        order = message.get("order") or {}
        if (order.get("id") or "").strip().lower() == "error":
            continue
        state = (order.get("state") or "").strip().upper()
        has_catalog = _has_policy_catalog_data(message)
        has_otp_flag = _has_otp_verified_signal(order)
        if has_otp_flag and (has_catalog or state == "COMPLETED"):
            return True
        if state == "COMPLETED" and has_catalog:
            return True
    return False


def _verify_pmfby_otp_with_bap(
    *,
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    inquiry_type: str = "policy_status",
    year: Optional[str] = None,
    season: str = "Kharif",
) -> tuple[bool, str]:
    """Call BAP `/status` and require a real OTP-verified policy response."""
    otp_str = _validate_otp(otp)
    phone = _validate_registered_mobile(phone_number)
    status_year = (year or str(datetime.now(timezone.utc).year)).strip()
    status_season = _season_name_for_status_api(season)
    transaction_id = generate_transaction_id(ctx.deps.session_id, phone_number)
    payload = _payload_status_verify_otp(
        transaction_id=transaction_id,
        otp=otp_str,
        phone=phone,
        inquiry_type=inquiry_type,
        year=status_year,
        season=status_season,
    )
    response = _post_json_logged(_bap_url("status"), payload, "[PMFBY_GRIEVANCE_OTP_STATUS]")

    if response.status_code != 200:
        return False, f"OTP verification failed (service unavailable). Status code: {response.status_code}"

    response_text = response.text.strip()
    if not response_text:
        return False, "OTP verification failed. Please re-check the OTP and try again."

    try:
        response_json = response.json()
    except json.JSONDecodeError:
        return False, "OTP verification failed. Please re-check the OTP and try again."

    error_message = _bap_status_error_message(response_json)
    if error_message:
        return False, f"OTP verification failed. {error_message}"

    if not _pmfby_otp_status_verified(response_json):
        return False, "OTP verification failed. Please re-check the OTP and try again."

    return True, "OTP verified."


class PMfbyGrievanceInitRequest(BaseModel):
    """Beckn `/init` body for `pmfby-grievance` submit."""

    transaction_id: str
    phone_number: str
    complaint_date: str = Field(default_factory=_today_yyyy_mm_dd)
    receipt_source_id: str = PMFBY_GRIEVANCE_RECEIPT_SOURCE_ID
    ticket_category_id: str = TICKET_CATEGORY_ID
    ticket_sub_category_id: str = TICKET_SUB_CATEGORY_ID
    request_year: str
    request_season: str
    application_no: str
    grievance_description: str

    def get_payload(self) -> Dict[str, Any]:
        phone = normalize_phone_for_api(self.phone_number)
        tags: List[Dict[str, Any]] = [
            ("request_type", "submit_grievance"),
            ("phone_number", phone),
            ("complaint_date", str(self.complaint_date)),
            ("receipt_source_id", str(self.receipt_source_id)),
            ("ticket_category_id", str(self.ticket_category_id)),
            ("ticket_sub_category_id", str(self.ticket_sub_category_id)),
            ("request_year", str(self.request_year)),
            ("request_season", str(self.request_season)),
            ("application_no", str(self.application_no)),
            ("grievance_description", str(self.grievance_description)),
        ]
        tag_objs = [{"descriptor": {"code": code}, "value": val} for code, val in tags]
        return {
            "context": _beckn_context(transaction_id=self.transaction_id, action="init"),
            "message": {
                "order": {
                    "provider": {"id": "pmfby-grievance"},
                    "items": [{"id": "pmfby-grievance"}],
                    "fulfillments": [{"customer": {"person": {"tags": tag_objs}}}],
                }
            },
        }


# --------------------------------------------------------------------------------------
# Response parsing (grievance submit)
# --------------------------------------------------------------------------------------


class Descriptor(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None


class TagListItem(BaseModel):
    descriptor: Optional[Descriptor] = None
    value: Optional[str] = None
    display: bool = True


class Tag(BaseModel):
    descriptor: Optional[Descriptor] = None
    list: Optional[List[TagListItem]] = None
    display: bool = True


class InitOrder(BaseModel):
    tags: Optional[List[Tag]] = None


class CatalogItem(BaseModel):
    id: Optional[str] = None
    tags: Optional[List[Tag]] = None


class CatalogProvider(BaseModel):
    id: Optional[str] = None
    items: Optional[List[CatalogItem]] = None


class Catalog(BaseModel):
    providers: Optional[List[CatalogProvider]] = None


class InitMessage(BaseModel):
    order: Optional[InitOrder] = None
    catalog: Optional[Catalog] = None


class InitResponseItem(BaseModel):
    message: Optional[InitMessage] = None


class InitResponse(BaseModel):
    responses: List[InitResponseItem] = []

    def format_grievance_result(self) -> str:
        if not self.responses:
            return "PMFBY grievance service returned no response. Please try again."

        for r in self.responses:
            order = (r.message.order if r.message and r.message.order else None) if r.message else None
            if not order or not order.tags:
                continue
            for tag in order.tags:
                if not tag.display:
                    continue
                tag_code = (tag.descriptor.code if tag.descriptor else None) or ""
                if tag_code != "grievance-response" or not tag.list:
                    continue

                kv: Dict[str, str] = {}
                for li in tag.list:
                    if not li.display or not li.descriptor or not li.descriptor.code or li.value is None:
                        continue
                    kv[str(li.descriptor.code)] = str(li.value)

                status = kv.get("status")
                ticket_no = kv.get("ticket-no") or kv.get("ticket_no")
                ticket_id = kv.get("ticket-id") or kv.get("ticket_id")
                message = kv.get("message")

                parts: List[str] = []
                if status:
                    parts.append(f"Status: {status}")
                if ticket_no:
                    parts.append(f"Ticket Number: {ticket_no}")
                if ticket_id:
                    parts.append(f"Ticket ID: {ticket_id}")
                if message:
                    parts.append(f"Message: {message}")
                return "\n".join(parts) if parts else "PMFBY grievance submitted. Please note the ticket details shared by the system."

        return "PMFBY grievance submitted, but ticket details were not found in the response."

    def format_status_result(self) -> str:
        """Format grievance status from `/search` (`on_search` / `status_grievance`) responses."""
        if not self.responses:
            return "No grievance status found for this ticket."

        lines: List[str] = []
        skip_codes = {"responseDynamic", "request_type"}

        for r in self.responses:
            if not r.message or not r.message.catalog or not r.message.catalog.providers:
                continue
            for provider in r.message.catalog.providers:
                if not provider.items:
                    continue
                for item in provider.items:
                    if not item.tags:
                        continue
                    for tag in item.tags:
                        if not tag.display:
                            continue
                        tag_code = (tag.descriptor.code if tag.descriptor else None) or ""
                        if tag_code != "grievance_status_fetched" or not tag.list:
                            continue
                        for li in tag.list:
                            if not li.display or not li.descriptor or li.value is None:
                                continue
                            code = li.descriptor.code or ""
                            if code in skip_codes:
                                continue
                            name = li.descriptor.name or li.descriptor.code or "Detail"
                            lines.append(f"{name}: {li.value}")

        if lines:
            return "\n".join(lines)
        return "No grievance status found for this ticket."


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


@observe(name="tool:initiate_pmfby_grievance_otp", as_type="tool")
def initiate_pmfby_grievance_otp(ctx: RunContext[FarmerContext], phone_number: str) -> str:
    """Send OTP to the farmer's registered mobile for PMFBY grievance filing.

    Call this as soon as the farmer provides a **10-digit** mobile number.
    After success, ask for the 6-digit OTP and call `check_pmfby_grievance_otp`.
    Do not ask for application number or complaint details before OTP is verified.

    Args:
        phone_number: 10-digit mobile registered with PMFBY (not a 6-digit OTP).
    """
    try:
        phone = _validate_registered_mobile(phone_number)
        transaction_id = generate_transaction_id(ctx.deps.session_id, phone_number)
        payload = _payload_get_otp_grievance_flow(transaction_id=transaction_id, phone=phone)
        url = _bap_url("init")
        response = _post_json_logged(url, payload, "[PMFBY_GRIEVANCE_OTP_INIT]")

        if response.status_code != 200:
            return f"PMFBY OTP init service unavailable. Status code: {response.status_code}"

        return "OTP has been sent to the registered mobile number. Please share the 6-digit OTP to proceed with grievance lodging."

    except httpx.TimeoutException:
        return "PMFBY OTP init request timed out. Please try again later."
    except httpx.RequestError as e:
        return f"PMFBY OTP init request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error("initiate_pmfby_grievance_otp: %s", e)
        raise ModelRetry(f"Unexpected error in PMFBY OTP init request. {str(e)}") from e


@observe(name="tool:check_pmfby_grievance_otp", as_type="tool")
def check_pmfby_grievance_otp(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    inquiry_type: str = "policy_status",
    year: str = "",
    season: str = "Kharif",
) -> str:
    """Verify the 6-digit OTP for PMFBY grievance filing via BAP `/status`.

    Call only **after** `initiate_pmfby_grievance_otp` succeeded and the farmer shares
    their OTP. Requires `otp_verified` plus policy data in the BAP response.
    Do not proceed to collect grievance details unless this tool returns OTP verified.

    Args:
        otp: 6-digit OTP from SMS (not the mobile number).
        phone_number: Same 10-digit mobile used in `initiate_pmfby_grievance_otp`.
        inquiry_type: Defaults to policy_status.
        year: Crop year for BAP status check; defaults to current year.
        season: Kharif, Rabi, or Summer; defaults to Kharif.
    """
    try:
        verified, message = _verify_pmfby_otp_with_bap(
            ctx=ctx,
            otp=otp,
            phone_number=phone_number,
            inquiry_type=inquiry_type,
            year=year or None,
            season=season,
        )
        if not verified:
            return message
        return (
            "OTP verified. Please share your PMFBY application number, "
            "crop season and year, and your complaint details."
        )

    except httpx.TimeoutException:
        return "OTP verification request timed out. Please try again."
    except httpx.RequestError as e:
        return f"OTP verification request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error("check_pmfby_grievance_otp: %s", e)
        raise ModelRetry(f"Unexpected error during OTP verification. {str(e)}") from e


@observe(name="tool:pmfby_grievance_status", as_type="tool")
def pmfby_grievance_status(
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

        ticket = _require_nonempty(
            grievance_support_ticket_no,
            "Please share your PMFBY grievance support ticket number.",
        )

        transaction_id = generate_transaction_id(
            ctx.deps.session_id, f"{phone}:{ticket}"
        )
        payload = _payload_grievance_status(
            transaction_id=transaction_id,
            phone=phone,
            grievance_support_ticket_no=ticket,
        )
        url = _bap_url("search")
        response = _post_json_logged(url, payload, "[PMFBY_GRIEVANCE_STATUS]")

        if response.status_code != 200:
            return f"PMFBY grievance status service unavailable. Status code: {response.status_code}"

        response_text = response.text.strip()
        if not response_text:
            return "PMFBY grievance status service returned an empty response. Please try again."

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            return "PMFBY grievance status service returned a non-JSON response. Please try again."

        return InitResponse.model_validate(response_json).format_status_result()

    except httpx.TimeoutException:
        return "PMFBY grievance status request timed out. Please try again later."
    except httpx.RequestError as e:
        return f"PMFBY grievance status request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error("pmfby_grievance_status: %s", e)
        raise ModelRetry(f"Unexpected error checking PMFBY grievance status. {str(e)}") from e


@observe(name="tool:pmfby_submit_grievance", as_type="tool")
def pmfby_submit_grievance(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    request_year: str,
    request_season: str,
    application_no: str,
    grievance_description: str,
) -> str:
    """Submit PMFBY grievance via Beckn `/init` after OTP verification.

    Call only after `check_pmfby_grievance_otp` returned OTP verified and the farmer
    has provided application number, season/year, and complaint description.
    """
    try:
        _validate_registered_mobile(phone_number)
        request_year = to_ascii_digits(_require_nonempty(request_year, "Please share the request year."))
        raw_season = _require_nonempty(request_season, "Please share the request season.")

        season_api = _normalize_request_season_for_pmfby_api(raw_season)
        if season_api not in ("1", "2", "3"):
            raise ModelRetry(
                "Ask which crop season the PMFBY policy is for — **Kharif**, **Rabi**, or **Zaid / summer** "
                "(or the same in the farmer's language). Do not ask them to memorize number codes; "
                "pass the season name to this tool."
            )

        verified, otp_message = _verify_pmfby_otp_with_bap(
            ctx=ctx,
            otp=otp,
            phone_number=phone_number,
            year=request_year.strip(),
            season=raw_season,
        )
        if not verified:
            return otp_message

        if not application_no or not str(application_no).strip():
            raise ModelRetry("Please share your PMFBY application number.")
        application_no = to_ascii_digits(application_no)

        if not grievance_description or len(grievance_description.strip()) < 10:
            raise ModelRetry("Please provide a brief grievance description (at least 10 characters).")

        transaction_id = generate_transaction_id(ctx.deps.session_id, phone_number)
        payload = PMfbyGrievanceInitRequest(
            transaction_id=transaction_id,
            phone_number=phone_number,
            request_year=request_year.strip(),
            request_season=season_api,
            application_no=application_no.strip(),
            grievance_description=grievance_description.strip(),
        ).get_payload()

        url = _bap_url("init")
        response = _post_json_logged(url, payload, "[PMFBY_GRIEVANCE_SUBMIT]")

        if response.status_code != 200:
            return f"PMFBY grievance service unavailable. Status code: {response.status_code}"

        response_text = response.text.strip()
        if not response_text:
            return "PMFBY grievance submitted. Please wait for the ticket details."

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            return "PMFBY grievance submitted, but the service returned a non-JSON response."

        return InitResponse.model_validate(response_json).format_grievance_result()

    except httpx.TimeoutException:
        return "PMFBY grievance request timed out. Please try again later."
    except httpx.RequestError as e:
        return f"PMFBY grievance request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error("pmfby_submit_grievance: %s", e)
        raise ModelRetry(f"Unexpected error in PMFBY grievance submission. {str(e)}") from e