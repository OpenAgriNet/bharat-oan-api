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
from helpers.utils import get_logger
from langfuse import observe
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext

logger = get_logger(__name__)

# --------------------------------------------------------------------------------------
# Integration values
# --------------------------------------------------------------------------------------

def _today_yyyy_mm_dd() -> str:
    return datetime.now(timezone.utc).date().isoformat()

# Keep these hardcoded (do not ask the user).
TICKET_CATEGORY_ID = "3"
TICKET_SUB_CATEGORY_ID = "10"


def generate_transaction_id(session_id: str, key: str) -> str:
    """Generate a consistent transaction ID across init calls."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, (session_id + key)))


def normalize_phone_for_api(phone: str) -> str:
    """Strip to digits only. BAP expects 10-digit Indian number (no country code)."""
    digits = "".join(c for c in str(phone).strip() if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits if digits else phone.strip()


def _validate_otp(otp: str) -> str:
    otp_str = str(otp).strip() if otp else ""
    if not otp_str:
        raise ModelRetry("Invalid OTP. Please provide the 6-digit OTP received via SMS.")
    digits = "".join(c for c in otp_str if c.isdigit())
    if len(digits) != 6:
        raise ModelRetry("PMFBY OTP must be exactly 6 digits. Please ask for the 6-digit OTP received on mobile.")
    return digits


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


class PMfbyGrievanceInitRequest(BaseModel):
    """Builds the Beckn `/init` request for submitting a PMFBY grievance."""

    transaction_id: str
    phone_number: str
    complaint_date: str = Field(default_factory=_today_yyyy_mm_dd)
    receipt_source_id: str
    ticket_category_id: str = TICKET_CATEGORY_ID
    ticket_sub_category_id: str = TICKET_SUB_CATEGORY_ID
    request_year: str
    request_season: str
    application_no: str
    grievance_description: str

    def get_payload(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        timestamp_str = str(int(now.timestamp()))
        phone = normalize_phone_for_api(self.phone_number)
        return {
            "context": {
                "domain": "schemes:vistaar",
                "action": "init",
                "version": "1.1.0",
                "bap_id": os.getenv("BAP_ID"),
                "bap_uri": os.getenv("BAP_URI"),
                "bpp_id": os.getenv("BPP_ID"),
                "bpp_uri": os.getenv("BPP_URI"),
                "transaction_id": self.transaction_id,
                "message_id": str(uuid.uuid4()),
                "timestamp": timestamp_str,
                "ttl": "PT10M",
                "location": {"country": {"code": "IND"}, "city": {"code": "*"}},
            },
            "message": {
                "order": {
                    "provider": {"id": "pmfby-grievance"},
                    "items": [{"id": "pmfby-grievance"}],
                    "fulfillments": [
                        {
                            "customer": {
                                "person": {
                                    "tags": [
                                        {"descriptor": {"code": "request_type"}, "value": "submit_grievance"},
                                        {"descriptor": {"code": "phone_number"}, "value": phone},
                                        {"descriptor": {"code": "complaint_date"}, "value": str(self.complaint_date)},
                                        {"descriptor": {"code": "receipt_source_id"}, "value": str(self.receipt_source_id)},
                                        {"descriptor": {"code": "ticket_category_id"}, "value": str(self.ticket_category_id)},
                                        {"descriptor": {"code": "ticket_sub_category_id"}, "value": str(self.ticket_sub_category_id)},
                                        {"descriptor": {"code": "request_year"}, "value": str(self.request_year)},
                                        {"descriptor": {"code": "request_season"}, "value": str(self.request_season)},
                                        {"descriptor": {"code": "application_no"}, "value": str(self.application_no)},
                                        {
                                            "descriptor": {"code": "grievance_description"},
                                            "value": str(self.grievance_description),
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                }
            },
        }


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


class InitMessage(BaseModel):
    order: Optional[InitOrder] = None


class InitResponseItem(BaseModel):
    message: Optional[InitMessage] = None


class InitResponse(BaseModel):
    responses: List[InitResponseItem] = []

    def format_grievance_result(self) -> str:
        """
        Expected (sample) response path:
        responses[0].message.order.tags[... descriptor.code == "grievance-response" ...].list -> status/ticket-no/message/...
        """
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
                    if not li.display or not li.descriptor or not li.descriptor.code:
                        continue
                    if li.value is None:
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


@observe(name="tool:initiate_pmfby_grievance_otp", as_type="tool")
def initiate_pmfby_grievance_otp(ctx: RunContext[FarmerContext], phone_number: str) -> str:
    """
    Initiate PMFBY OTP verification for grievance lodging.

    This mirrors the PMFBY status OTP init flow (`/init` with request_type=get_otp) so the
    platform can verify the farmer's mobile before lodging a grievance.
    """
    try:
        session_id = ctx.deps.session_id
        transaction_id = generate_transaction_id(session_id, phone_number)
        phone = normalize_phone_for_api(phone_number)

        payload = {
            "context": {
                "domain": "schemes:vistaar",
                "action": "init",
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
            },
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

        endpoint = os.getenv("BAP_ENDPOINT", "").rstrip("/") + "/init"
        if not endpoint or endpoint == "/init":
            raise ModelRetry("BAP endpoint is not configured. Set BAP_ENDPOINT.")

        logger.info(f"[PMFBY GRIEVANCE OTP INIT] Request URL: {endpoint}")
        logger.info(f"[PMFBY GRIEVANCE OTP INIT] Request Payload: {json.dumps(payload, indent=2)}")
        response = httpx.post(endpoint, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)
        logger.info(f"[PMFBY GRIEVANCE OTP INIT] Response Status: {response.status_code}")
        logger.info(f"[PMFBY GRIEVANCE OTP INIT] Response Payload: {response.text}")

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
        logger.error(f"Unexpected error in initiate_pmfby_grievance_otp: {e}")
        raise ModelRetry(f"Unexpected error in PMFBY OTP init request. {str(e)}")


@observe(name="tool:check_pmfby_grievance_otp", as_type="tool")
def check_pmfby_grievance_otp(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
) -> str:
    """
    Verify PMFBY OTP before grievance lodging.

    Calls Beckn `action=status` with `message.order_id` set to the 6-digit OTP, provider `pmfby-agri`,
    and fulfillments containing only `customer.contact.phone` (same deterministic `transaction_id`
    as `initiate_pmfby_grievance_otp`).

    On HTTP 200, success is inferred unless the response body contains known OTP-failure phrases
    (e.g. invalid/expired OTP).
    """
    try:
        otp_str = _validate_otp(otp)
        session_id = ctx.deps.session_id
        transaction_id = generate_transaction_id(session_id, phone_number)
        phone = normalize_phone_for_api(phone_number)

        payload: Dict[str, Any] = {
            "context": {
                "domain": "schemes:vistaar",
                "action": "status",
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
            },
            "message": {
                "order_id": otp_str,
                "order": {
                    "id": "order-1",
                    "provider": {"id": "pmfby-agri"},
                    "items": [{"id": "pmfby"}],
                    "fulfillments": [{"customer": {"contact": {"phone": phone}}}],
                },
            },
        }

        endpoint = os.getenv("BAP_ENDPOINT", "").rstrip("/") + "/status"
        if not endpoint or endpoint == "/status":
            raise ModelRetry("BAP endpoint is not configured. Set BAP_ENDPOINT.")

        logger.info(f"[PMFBY GRIEVANCE OTP STATUS] Request URL: {endpoint}")
        logger.info(f"[PMFBY GRIEVANCE OTP STATUS] Request Payload: {json.dumps(payload, indent=2)}")
        response = httpx.post(endpoint, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)
        logger.info(f"[PMFBY GRIEVANCE OTP STATUS] Response Status: {response.status_code}")
        logger.info(f"[PMFBY GRIEVANCE OTP STATUS] Response Payload: {response.text}")

        if response.status_code != 200:
            return f"OTP verification failed (service unavailable). Status code: {response.status_code}"

        blob = response.text.lower()
        if any(s in blob for s in _OTP_FAILURE_SUBSTRINGS):
            return "OTP verification failed. Please re-check the OTP and try again."

        return "OTP verified. Please share your PMFBY application number…"

    except httpx.TimeoutException:
        return "OTP verification request timed out. Please try again."
    except httpx.RequestError as e:
        return f"OTP verification request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Unexpected error in check_pmfby_grievance_otp: {e}")
        raise ModelRetry(f"Unexpected error during OTP verification. {str(e)}")


@observe(name="tool:pmfby_submit_grievance", as_type="tool")
def pmfby_submit_grievance(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    receipt_source_id: str,
    request_year: str,
    request_season: str,
    application_no: str,
    grievance_description: str,
) -> str:
    """
    Submit a PMFBY grievance through the Beckn `/init` flow.

    Fields to collect from the user (after OTP verification):
    - phone_number
    - receipt_source_id
    - request_year
    - request_season
    - application_no
    - grievance_description

    Hardcoded:
    - complaint_date = 2026-01-20
    - ticket_category_id / ticket_sub_category_id
    """
    try:
        _ = _validate_otp(otp)

        if not phone_number or not str(phone_number).strip():
            raise ModelRetry("Please share your registered mobile number.")

        if not receipt_source_id or not str(receipt_source_id).strip():
            raise ModelRetry("Please share the receipt source ID.")

        if not request_year or not str(request_year).strip():
            raise ModelRetry("Please share the request year.")

        if not request_season or not str(request_season).strip():
            raise ModelRetry("Please share the request season.")

        if not application_no or not str(application_no).strip():
            raise ModelRetry("Please share your PMFBY application number.")

        if not grievance_description or len(grievance_description.strip()) < 10:
            raise ModelRetry("Please provide a brief grievance description (at least 10 characters).")

        session_id = ctx.deps.session_id
        transaction_id = generate_transaction_id(session_id, phone_number)

        payload = PMfbyGrievanceInitRequest(
            transaction_id=transaction_id,
            phone_number=phone_number,
            receipt_source_id=receipt_source_id,
            request_year=request_year,
            request_season=request_season,
            application_no=application_no,
            grievance_description=grievance_description.strip(),
        ).get_payload()

        endpoint = os.getenv("BAP_ENDPOINT", "").rstrip("/") + "/init"
        if not endpoint or endpoint == "/init":
            raise ModelRetry("BAP endpoint is not configured. Set BAP_ENDPOINT.")

        logger.info(f"[PMFBY GRIEVANCE INIT] Request URL: {endpoint}")
        logger.info(f"[PMFBY GRIEVANCE INIT] Request Payload: {json.dumps(payload, indent=2)}")
        response = httpx.post(endpoint, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)
        logger.info(f"[PMFBY GRIEVANCE INIT] Response Status: {response.status_code}")
        logger.info(f"[PMFBY GRIEVANCE INIT] Response Payload: {response.text}")

        if response.status_code != 200:
            return f"PMFBY grievance service unavailable. Status code: {response.status_code}"

        response_text = response.text.strip()
        if not response_text:
            return "PMFBY grievance submitted. Please wait for the ticket details."

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            return "PMFBY grievance submitted, but the service returned a non-JSON response."

        parsed = InitResponse.model_validate(response_json)
        return parsed.format_grievance_result()

    except httpx.TimeoutException:
        return "PMFBY grievance request timed out. Please try again later."
    except httpx.RequestError as e:
        return f"PMFBY grievance request failed: {str(e)}"
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Unexpected error in pmfby_submit_grievance: {e}")
        raise ModelRetry(f"Unexpected error in PMFBY grievance submission. {str(e)}")

