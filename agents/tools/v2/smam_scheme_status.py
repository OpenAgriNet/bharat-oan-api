"""SMAM application status over Beckn v2: discover the SMAM resource, then one status call.
Rendering reuses v1's models so both versions show the model the same text."""
import json
import uuid
from typing import Any, Dict, List, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.smam_scheme_status import SmamSearchType, SmamStatusApiResponse, _normalize_search_value
from agents.tools.v2.aif import (
    ACTIVE,
    SCHEME_APPLICATION_TYPE,
    _actions,
    _first_resource,
    build_context,
    commitment_attributes,
    commitment_envelope,
    post_action,
    scheme_application_context,
)
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

logger = get_logger(__name__)

_BENEFITS_CODE = "smam-benefits"
_SCHEME_CODE = "SMAM"

_UNAVAILABLE = "SMAM application status service is unavailable. Please try again later."
_TIMED_OUT = "SMAM status request timed out. Please try again later."
_NO_APPLICATIONS = "No applications found"


async def _post(action: str, body: Dict[str, Any]) -> httpx.Response:
    return await post_action(action, body, who="SMAM")


def _unusable(found: Dict[str, Any]) -> Optional[str]:
    """Why this resource cannot serve a status call, or None when it can."""
    advertised = [entry.get("action") for entry in _actions(found)]
    if "status" not in advertised:
        return f"does not advertise status; catalog offers {advertised}"
    if not (found.get("offer") or {}).get("id"):
        return "is in no published offer; a commitment has to be made against one"
    return None


async def _discover(transaction_id: str) -> Optional[Dict[str, Any]]:
    """The SMAM resource with its catalog provider and offer, or None."""
    body = {
        "context": build_context("discover", transaction_id),
        "message": {"intent": {"filters": {
            "type": "jsonpath",
            "expression": f'$.catalogs[*].resources[*] ? (@.descriptor.code == "{_BENEFITS_CODE}")',
        }}},
    }
    response = await _post("discover", body)
    if response.status_code != 200:
        logger.error(f"SMAM v2 discover failed: HTTP {response.status_code} {response.text[:300]}")
        return None
    return _first_resource(response.json(), usable=_unusable, who="SMAM")


def _status_body(found: Dict[str, Any], search_value: str, transaction_id: str) -> Dict[str, Any]:
    scheme = ((found["resource"].get("resourceAttributes") or {}).get("scheme") or {}).get("code") or _SCHEME_CODE
    attributes = {
        "@context": scheme_application_context(),
        "@type": SCHEME_APPLICATION_TYPE,
        "informationMode": "OnDemand",
        "scheme": {"code": scheme},
        # SMAM works out whether this is a mobile or an application number; v1 never sent the kind.
        "searchValue": search_value,
    }
    return commitment_envelope("status", transaction_id, ACTIVE, attributes, found["offer"]["id"], found)


def _applications(data: Any) -> List[Dict[str, Any]]:
    """SMAM's data as a list, read the way provider-backend reads it: a list, one object, or JSON text."""
    if not data:
        return []
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def _tags(*pairs) -> List[Dict[str, Any]]:
    return [{"descriptor": {"code": code, "name": name}, "value": value} for code, name, value in pairs]


def _as_v1_item(implement: Dict[str, Any]) -> Dict[str, Any]:
    """One machine, shaped as provider-backend's on_search item: current status and history tags."""
    history = implement.get("StatusHistory") or []
    latest = history[0] if history else {}
    subsidy_id = str(implement.get("ImplementSubsidyID"))
    return {
        "id": subsidy_id,
        "descriptor": {"name": implement.get("ImplementName") or "", "code": subsidy_id},
        "tags": [
            {"descriptor": {"code": "implement-status", "name": "Implement Status"}, "list": _tags(
                ("current-status-code", "Current Status Code", "" if latest.get("StatusCode") is None else str(latest["StatusCode"])),
                ("current-status-text", "Current Status", latest.get("StatusText") or ""),
                ("current-status-date", "Status Date", latest.get("StatusDate") or ""))},
            {"descriptor": {"code": "status-history", "name": "Status History"}, "list": _tags(*(
                (f"history-{i}", f"Step {i}", json.dumps({"status_code": step.get("StatusCode"),
                                                          "status_text": step.get("StatusText"),
                                                          "status_date": step.get("StatusDate")}))
                for i, step in enumerate(history, 1)))},
        ],
    }


def _as_v1_on_search(answer: Dict[str, Any], search_value: str) -> Dict[str, Any]:
    """SMAM's answer in provider-backend's on_search shape, so v1's rendering applies unchanged."""
    applications = _applications(answer.get("data"))
    status = "Success" if answer.get("success") else "Failed"
    message = answer.get("message") or ""
    if not applications:
        status, message = "Failed", message or _NO_APPLICATIONS
    providers = [{
        "id": str(app.get("ApplicationID")),
        "descriptor": {"name": app.get("ApplicationRefNo") or str(app.get("ApplicationID")), "code": str(app.get("ApplicationID"))},
        "items": [_as_v1_item(implement) for implement in app.get("Implements") or []],
    } for app in applications]
    return {"catalog": {
        "descriptor": {"name": "SMAM Application Status", "code": "smam"},
        # provider-backend recorded every lookup as application_no, the kind it fell back to.
        "tags": [{"descriptor": {"code": "search-context", "name": "Search Context"}, "list": _tags(
            ("status", "Status", status), ("message", "Message", message),
            ("search-type", "Search Type", "application_no"), ("search-value", "Search Value", search_value))}],
        "providers": providers,
    }}


def _rendered(answer: Dict[str, Any], search_value: str) -> str:
    on_search = _as_v1_on_search(answer, search_value)
    logger.info(f"SMAM v2 {len(on_search['catalog']['providers'])} applications, success={answer.get('success')}")
    return str(SmamStatusApiResponse.model_validate({"responses": [{"context": {"action": "on_search"}, "message": on_search}]}))


async def _lookup(search_value: str, transaction_id: str) -> str:
    """Discover the SMAM resource, ask its status, and render the answer as v1 did."""
    found = await _discover(transaction_id)
    if not found:
        return _UNAVAILABLE
    response = await _post("status", _status_body(found, search_value, transaction_id))
    if response.status_code != 200:
        logger.error(f"SMAM v2 status refused: HTTP {response.status_code} {response.text[:300]}")
        return _UNAVAILABLE
    return _rendered(commitment_attributes(response), search_value)


@observe(name="tool:check_smam_scheme_status", as_type="tool")
async def check_smam_scheme_status(
    ctx: RunContext[FarmerContext],
    search_type: SmamSearchType,
    search_value: str,
) -> str:
    """Check SMAM (Sub-Mission on Agricultural Mechanization) application / beneficiary status.

    Use this tool when the farmer wants to check SMAM subsidy or application status.

    Args:
        search_type: Lookup type — `mobile` (10-digit Indian) or `application_no` (application reference
            or numeric id).
        search_value: Identifier shared by the farmer (no placeholders). Numeric values are
            digit-normalized when applicable (e.g. `+91` / leading `0` removed for mobile-shaped inputs).

    Returns:
        Human-readable status with implement details and status history (when available).
    """
    try:
        normalized = _normalize_search_value(search_type, search_value)
    except ValueError as e:
        return str(e)
    transaction_id = str(uuid.uuid4())
    lf_update_current_observation(
        input={"search_type": search_type, "has_search_value": bool(normalized)},
        metadata={"tool": "smam.status", "search_type": search_type, "beckn_version": "v2", "transaction_id": transaction_id},
    )
    try:
        return await _lookup(normalized, transaction_id)
    except httpx.TimeoutException as e:
        logger.error(f"SMAM v2 status timed out: {e}")
        return _TIMED_OUT
    except httpx.RequestError as e:
        logger.error(f"SMAM v2 status request failed: {e}")
        return f"SMAM status request failed: {e!s}"
    except UnexpectedModelBehavior:
        return "SMAM status is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error in SMAM v2 status check: {e}")
        raise ModelRetry(f"Unexpected error in SMAM status request. {e!s}") from e
