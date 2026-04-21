"""
PM-KISAN Grievance Tools (pmkisan_submit_grievance, pmkisan_grievance_status)

"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal
from pathlib import Path

import httpx
from app.config import DEFAULT_HTTP_TIMEOUT
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from agents.deps import FarmerContext
from langfuse import observe
from helpers.utils import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------------------
# Config / Constants
# --------------------------------------------------------------------------------------

BAP_ENDPOINT = os.getenv("BAP_ENDPOINT")
BAP_ID = os.getenv("BAP_ID")
BAP_URI = os.getenv("BAP_URI")
BPP_ID = os.getenv("BPP_ID")
BPP_URI = os.getenv("BPP_URI")

# Mapping file: human-friendly grievance labels -> backend codes
_DEFAULT_GRIEVANCE_JSON_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "grievance_types.json"
)
_GRIEVANCE_JSON_PATH = os.getenv("GRIEVANCE_TYPES_PATH", str(_DEFAULT_GRIEVANCE_JSON_PATH))

def _load_grievance_mapping(path: str) -> Dict[str, str]:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("grievance_types.json must be an object of {label: code}")
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logger.error(f"Failed to load grievance mapping at '{path}': {e}")
        return {}

GRIEVANCE_MAPPING: Dict[str, str] = _load_grievance_mapping(_GRIEVANCE_JSON_PATH)
GRIEVANCE_TYPES: List[str] = list(GRIEVANCE_MAPPING.keys())

# -----------------------
# Response formatting
# -----------------------

def _format_http_response_raw(response: httpx.Response, *, max_chars: int = 8000) -> str:
    """
    Return the network response as a string (prefer pretty JSON), truncated to max_chars.
    """
    try:
        body: str
        try:
            body = json.dumps(response.json(), ensure_ascii=False, indent=2)
        except Exception:
            body = response.text
        body = (body or "").strip()
        if len(body) > max_chars:
            body = body[:max_chars] + "\n... (truncated)"
        return body
    except Exception:
        # Last-resort: never crash while formatting tool output
        return (response.text or "").strip()

def _pick(d: Dict[str, Any], *keys: str) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

class GatewayContext(BaseModel):
    action: Optional[str] = None
    timestamp: Optional[str] = None
    message_id: Optional[str] = None
    transaction_id: Optional[str] = None
    ttl: Optional[str] = None
    domain: Optional[str] = None
    version: Optional[str] = None
    bap_id: Optional[str] = None
    bap_uri: Optional[str] = None
    bpp_id: Optional[str] = None
    bpp_uri: Optional[str] = None
    location: Optional[Dict[str, Any]] = None


class TagDescriptor(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None


class TagListItem(BaseModel):
    descriptor: Optional[TagDescriptor] = None
    value: Optional[str] = None
    display: Optional[bool] = None


class TagGroup(BaseModel):
    descriptor: Optional[TagDescriptor] = None
    list: Optional[List[TagListItem]] = None
    display: Optional[bool] = None


class ProviderRef(BaseModel):
    id: Optional[str] = None


class ItemRef(BaseModel):
    id: Optional[str] = None


class OrderOut(BaseModel):
    provider: Optional[ProviderRef] = None
    items: Optional[List[ItemRef]] = None
    tags: Optional[List[TagGroup]] = None
    type: Optional[str] = None


class MessageOut(BaseModel):
    order: Optional[OrderOut] = None


class GatewaySubResponse(BaseModel):
    context: Optional[GatewayContext] = None
    message: Optional[MessageOut] = None


class GatewayResponse(BaseModel):
    context: Optional[GatewayContext] = None
    responses: Optional[List[GatewaySubResponse]] = None
    error: Optional[Any] = None

    def _order_tags_kv(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        sub = (self.responses or [None])[0]
        order = None
        if sub and sub.message and sub.message.order:
            order = sub.message.order
        if not order or not order.tags:
            return out
        for group in order.tags:
            if not group or not group.list:
                continue
            for item in group.list:
                if not item or not item.descriptor or not item.descriptor.code:
                    continue
                if item.value is None:
                    continue
                out[str(item.descriptor.code)] = str(item.value)
        return out

    def __str__(self) -> str:
        # Mirror other tools: readable summary from validated JSON
        lines: List[str] = []
        tx = self.context.transaction_id if self.context else None
        mid = self.context.message_id if self.context else None
        if tx:
            lines.append(f"transaction_id: {tx}")
        if mid:
            lines.append(f"message_id: {mid}")

        tags = self._order_tags_kv()
        if tags:
            # These codes match what the gateway returns in your sample
            if "status" in tags:
                lines.append(f"Status: {tags.get('status', '').strip()}")
            if "grievance-id" in tags:
                lines.append(f"Grievance ID: {tags.get('grievance-id', '').strip()}")
            if "identity-no" in tags:
                lines.append(f"Identity No: {tags.get('identity-no', '').strip()}")
            if "grievance-type" in tags:
                lines.append(f"Grievance Type: {tags.get('grievance-type', '').strip()}")
            if "message" in tags and tags["message"]:
                lines.append(f"Message: {tags.get('message', '').strip()}")

        if self.error is not None:
            lines.append(f"Error: {json.dumps(self.error, ensure_ascii=False)}")

        if not lines:
            return "Request accepted."
        return "\n".join([l for l in lines if l.strip()]).strip()


def _format_grievance_response_formatted(response: httpx.Response) -> str:
    """
    Format the response the same way other tools do:
    parse JSON -> validate -> return str(model).
    """
    resp_text = (response.text or "").strip()
    if not resp_text:
        return "Service returned empty response. Please try again later."

    try:
        parsed = response.json()
    except Exception:
        return resp_text

    try:
        model = GatewayResponse.model_validate(parsed)
        return str(model)
    except ValidationError:
        # If response shape evolves, don't break the tool — fallback to pretty JSON
        try:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return resp_text

# -----------------------
# Shared Helpers
# -----------------------

def generate_transaction_id(session_id: str, identifier: str) -> str:
    """Generate a stable transaction ID for a grievance flow."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, (session_id + identifier + "grievance")))

# -----------------------
# Vistaar Models
# -----------------------

class Descriptor(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    short_desc: Optional[str] = None

class TagItem(BaseModel):
    descriptor: Descriptor
    value: Optional[str] = None
    display: bool = True

class Tag(BaseModel):
    display: bool = True
    descriptor: Descriptor
    list: Optional[List[TagItem]] = None

class Person(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[Tag]] = None

class Contact(BaseModel):
    phone: Optional[str] = None

class Customer(BaseModel):
    person: Optional[Person] = None
    contact: Optional[Contact] = None

class Fulfillment(BaseModel):
    customer: Optional[Customer] = None

class Item(BaseModel):
    id: str

class Provider(BaseModel):
    id: str

class Order(BaseModel):
    provider: Provider
    items: List[Item]
    fulfillments: Optional[List[Fulfillment]] = None

class Context(BaseModel):
    domain: str = "schemes:vistaar"
    action: str
    version: str = "1.1.0"
    bap_id: Optional[str] = BAP_ID
    bap_uri: Optional[str] = BAP_URI
    bpp_id: Optional[str] = BPP_ID
    bpp_uri: Optional[str] = BPP_URI
    transaction_id: str
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
    location: Dict[str, Any] = {
        "country": {"code": "IND"},
        "city": {"code": "*"}
    }

class GrievanceInitRequest(BaseModel):
    context: Context
    message: Dict[str, Any]

    @classmethod
    def build(
        cls,
        transaction_id: str,
        identifier_value: str,
        identifier_type: Literal["reg-number", "aad-number"],
        grievance_type_code: str,
        grievance_description: str,
        customer_name: str = "Customer Name",
        phone: str = ""
    ) -> "GrievanceInitRequest":
        identifier_name = "Registration Number" if identifier_type == "reg-number" else "Aadhaar Number"
        return cls(
            context=Context(action="init", transaction_id=transaction_id),
            message={
                "order": {
                    "provider": {"id": "pmkisan-greviance"},
                    "items": [{"id": "pmkisan-greviance"}],
                    "fulfillments": [
                        {
                            "customer": {
                                "person": {
                                    "name": customer_name,
                                    "tags": [
                                        {
                                            "display": True,
                                            "descriptor": {"name": "Registration Details", "code": "reg-details"},
                                            "list": [
                                                {
                                                    "descriptor": {"name": identifier_name, "code": identifier_type},
                                                    "value": identifier_value,
                                                    "display": True
                                                }
                                            ]
                                        },
                                        {
                                            "display": True,
                                            "descriptor": {"name": "Grievance Details", "code": "grievance-details"},
                                            "list": [
                                                {
                                                    "descriptor": {"name": "Grievance Type", "code": "grievance-type"},
                                                    "value": grievance_type_code,
                                                    "display": True
                                                },
                                                {
                                                    "descriptor": {"name": "Grievance Description", "code": "grievance-description"},
                                                    "value": grievance_description,
                                                    "display": True
                                                }
                                            ]
                                        }
                                    ]
                                },
                                "contact": {"phone": phone} if phone else {}
                            }
                        }
                    ]
                }
            }
        )

# --------------------------------------------------------------------------------------
# Exported Tools
# --------------------------------------------------------------------------------------

@observe(name="tool:pmkisan_submit_grievance", as_type="tool")
def pmkisan_submit_grievance(
    ctx: RunContext[FarmerContext],
    reg_no: str,
    grievance_description: str,
    grievance_type: str,
    aadhaar_no: Optional[str] = None,
    raw: bool = False,
) -> str:
    """
    Submit a grievance to the PM-KISAN portal via Vistaar Network.

    Args:
        reg_no: PM-KISAN Registration Number (11-character alphanumeric string, e.g., 'BRXXXXXXXXX').
        grievance_description: Detailed description of the grievance.
        grievance_type: The type of grievance. Must be one of:
            "ACCOUNT_NUMBER_NOT_CORRECT", "ONLINE_APPLICATION_PENDING_FOR_APPROVAL",
            "INSTALLMENT_NOT_RECEIVED", "TRANSACTION_FAILED", "PROBLEM_IN_AADHAAR_CORRECTION",
            "GENDER_NOT_CORRECT", "PAYMENT_RELATED", "PROBLEM_IN_OTP_BASED_EKYC",
            "PROBLEM_IN_BIO_METRIC_BASED_EKYC", "PROBLEM_IN_FACIAL_BASED_EKYC"

    Returns:
        A confirmation message or an error if the submission failed.
    """
    try:
        identifier_value: Optional[str] = None
        identifier_type: Optional[Literal["reg-number", "aad-number"]] = None

        if aadhaar_no and aadhaar_no.strip():
            identifier_value = aadhaar_no.strip()
            identifier_type = "aad-number"
        elif reg_no and reg_no.strip():
            identifier_value = reg_no.strip()
            identifier_type = "reg-number"
        else:
            raise ModelRetry("Please provide either a PM-KISAN Registration Number or an Aadhaar Number.")

        if not grievance_type or grievance_type not in GRIEVANCE_MAPPING:
            choices = '", "'.join(GRIEVANCE_TYPES)
            raise ModelRetry(f'Invalid grievance type: "{grievance_type}". Please select from: "{choices}".')

        if not grievance_description or len(grievance_description.strip()) < 10:
            raise ModelRetry("Please provide a brief grievance description (at least 10 characters).")

        session_id = ctx.deps.session_id
        transaction_id = generate_transaction_id(session_id, identifier_value)
        
        phone = ""

        request_obj = GrievanceInitRequest.build(
            transaction_id=transaction_id,
            identifier_value=identifier_value,
            identifier_type=identifier_type,
            grievance_type_code=GRIEVANCE_MAPPING[grievance_type],
            grievance_description=grievance_description.strip(),
            phone=phone
        )
        payload = request_obj.model_dump(by_alias=True)
        
        if not BAP_ENDPOINT:
            raise ModelRetry("BAP_ENDPOINT is not configured in environment.")

        endpoint = f"{BAP_ENDPOINT.rstrip('/')}/init"
        logger.info(f"[PM KISAN GRIEVANCE] Request URL: {endpoint}")
        logger.info(f"[PM KISAN GRIEVANCE] Payload: {json.dumps(payload)}")

        response = httpx.post(
            endpoint,
            json=payload,
            timeout=DEFAULT_HTTP_TIMEOUT
        )

        logger.info(f"[PM KISAN GRIEVANCE] Response Status: {response.status_code}")
        logger.info(f"[PM KISAN GRIEVANCE] Response Body: {response.text[:500]}")

        if response.status_code != 200:
            logger.error(f"Grievance submission failed with status {response.status_code}")
            return _format_http_response_raw(response) if raw else _format_grievance_response_formatted(response)

        return _format_http_response_raw(response) if raw else _format_grievance_response_formatted(response)

    except httpx.TimeoutException:
        logger.error("Grievance submission timed out.")
        return "Grievance submission timed out. Please try again."
    except httpx.RequestError as e:
        logger.error(f"Grievance submission network error: {e}")
        return "Unable to reach grievance service. Please try again."
    except ModelRetry as e:
        return str(e)
    except Exception as e:
        logger.error(f"Unexpected error in pmkisan_submit_grievance: {e}")
        raise ModelRetry(f"Unexpected error while submitting grievance. {str(e)}")


@observe(name="tool:pmkisan_grievance_status", as_type="tool")
def pmkisan_grievance_status(ctx: RunContext[FarmerContext], reg_no: str, raw: bool = False) -> str:
    """
    Check the status of a previously submitted grievance using PM-KISAN Registration Number.

    Args:
        reg_no: PM-KISAN Registration Number (11-character alphanumeric, e.g., 'BRXXXXXXXXX').

    Returns:
        A status summary including the latest updates on the grievance.
    """
    try:
        if not reg_no or not reg_no.strip():
            raise ModelRetry("Please provide the PM-KISAN Registration Number to check grievance status.")

        reg_no = reg_no.strip()
        session_id = ctx.deps.session_id
        transaction_id = generate_transaction_id(session_id, reg_no)

        payload = {
            "context": Context(action="search", transaction_id=transaction_id).model_dump(by_alias=True),
            "message": {
                "intent": {
                    "category": {
                        "descriptor": {
                            "name": "grievance-agri",
                            "code": "grievance",
                        }
                    },
                    "order": {
                        "fulfillments": [
                            {
                                "customer": {
                                    "person": {
                                        "name": "Customer Name",
                                        "tags": [
                                            {
                                                "display": True,
                                                "descriptor": {
                                                    "name": "Registration Details",
                                                    "code": "reg-details",
                                                },
                                                "list": [
                                                    {
                                                        "descriptor": {
                                                            "name": "Registration Number",
                                                            "code": "reg-number",
                                                        },
                                                        "value": reg_no,
                                                        "display": True,
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                }
                            }
                        ]
                    },
                }
            }
        }

        if not BAP_ENDPOINT:
            return "Grievance status check is currently unavailable (BAP not configured)."

        endpoint = f"{BAP_ENDPOINT.rstrip('/')}/search"
        logger.info(f"[PM KISAN GRIEVANCE STATUS] Request URL: {endpoint}")
        
        response = httpx.post(
            endpoint,
            json=payload,
            timeout=DEFAULT_HTTP_TIMEOUT
        )

        if response.status_code != 200:
            return _format_http_response_raw(response) if raw else _format_grievance_response_formatted(response)

        return _format_http_response_raw(response) if raw else _format_grievance_response_formatted(response)

    except Exception as e:
        logger.error(f"Error in pmkisan_grievance_status: {e}")
        return "An error occurred while checking grievance status. Please try again later."