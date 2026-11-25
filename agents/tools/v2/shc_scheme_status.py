"""Soil Health Card reports over Beckn v2: discover the SHC resource, select the reports.
Rendering reuses v1's models so both versions show the model the same text."""
import base64
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.shc_scheme_status import Descriptor, Item, MediaResource, Provider, format_phone_number
from agents.tools.v2.aif import _first_resource
from agents.tools.v2.provider_openapi import From, SchemaUnavailable, filled_for
from agents.tools.v2.sathi_seed import AGRICULTURE_RESOURCE_CONTEXT
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)

_SCHEMA_TYPE = "openagrinet:AgricultureResource"
_SHC_REPORTS_CODE = "shc-reports"
_OFFER_ID = "offer:shc-reports"

# v1 dropped a report missing any of these, so v2 does too.
_REQUIRED_REPORT_PATHS = (
    "id", "reportData", "rdfValues", "reportData.parameterInfos",
    "rdfValues.fertilizerRecommendation_details", "rdfValues.deficiency",
)
_LAB_DESCRIPTION = "Govt Soil Testing Lab - Maharashtra, India"

_NO_DATA = "No data available."
_UNAVAILABLE = "Soil health card status service unavailable."
_TIMED_OUT = "Soil health card status request timed out. Please try again later."


def _adapter_url(action: str) -> str:
    base = os.getenv("OAN_ADAPTER_URL", "http://network-adapter:9201/consumer")
    return base.rstrip("/") + "/" + action


def _context(action: str, transaction_id: str) -> dict:
    """The v2 envelope. Single-network pilot, so sender and receiver match."""
    network_id = os.getenv("OAN_NETWORK_ID", "oan.dev")
    context = {
        "domain": os.getenv("OAN_DOMAIN", "agriculture"),
        "action": action,
        "version": "2.0.0",
        # The SHC catalog is public, so discover must ask the "*" audience.
        "networkId": os.getenv("OAN_PUBLIC_NETWORK_ID", "*") if action == "discover" else network_id,
        "senderId": network_id,
        "receiverId": network_id,
        "transactionId": transaction_id,
        "messageId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "schemaContext": [f"{AGRICULTURE_RESOURCE_CONTEXT}#{_SCHEMA_TYPE}"],
    }
    if action == "select":
        context["bppUri"] = f"https://{network_id}"
    return context


async def _post(action: str, body: dict) -> dict:
    url = _adapter_url(action)
    logger.info(f"SHC v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json()


def _discover_body(transaction_id: str) -> dict:
    return {
        "context": _context("discover", transaction_id),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": f'$.catalogs[*].resources[*] ? (@.descriptor.code == "{_SHC_REPORTS_CODE}")',
                }
            }
        },
    }


async def _discover_reports_resource(transaction_id: str) -> Optional[Dict[str, Any]]:
    """The SHC reports resource with its catalog provider, or None when nothing is published."""
    payload = await _post("discover", _discover_body(transaction_id))
    found = _first_resource(payload, who="SHC")
    if not found:
        logger.warning(f"SHC v2 discover found no {_SHC_REPORTS_CODE} resource")
        return None
    logger.info(
        f"SHC v2 discover found {found['resource'].get('id')} from provider "
        f"{found['provider'].get('id')}"
    )
    return found


async def _request(resource: dict, phone: str, cycle: str) -> dict:
    """The select request read off the contract the catalog names: SHC's own
    GraphQL query, fixed as a const there, and the variables it declares. The
    page size and locale v1 hardcoded are defaults in the contract now."""
    fields, unfillable = await filled_for(resource, "select", {From.MOBILE: phone, From.CYCLE: cycle})
    if unfillable:
        raise SchemaUnavailable(f"SHC select cannot be filled: {unfillable}")
    return fields


def _select_body(found: Dict[str, Any], fields: dict, transaction_id: str) -> dict:
    resource_id = found["resource"].get("id")
    return {
        "context": _context("select", transaction_id),
        "message": {
            "contract": {
                "status": {"code": "ACTIVE", "name": "active"},
                "commitments": [
                    {
                        "status": {"descriptor": {"code": "ACTIVE", "name": "active"}},
                        "resources": [
                            {
                                "id": resource_id,
                                "quantity": {"count": 1},
                                # The adapter adds SHC's bearer token; it is never sent from here.
                                "resourceAttributes": {
                                    "@context": AGRICULTURE_RESOURCE_CONTEXT,
                                    "@type": _SCHEMA_TYPE,
                                    **fields,
                                },
                            }
                        ],
                        "offer": {"id": _OFFER_ID, "resourceIds": [resource_id], "provider": found["provider"]},
                    }
                ],
            }
        },
    }


def _shc_answer(on_select: dict) -> dict:
    """SHC's GraphQL reply, carried in the first resource of the first commitment."""
    commitments = ((on_select.get("message") or {}).get("contract") or {}).get("commitments") or [{}]
    resources = commitments[0].get("resources") or [{}]
    return resources[0].get("resourceAttributes") or {}


def _missing_path(report: dict) -> Optional[str]:
    for path in _REQUIRED_REPORT_PATHS:
        node: Any = report
        for key in path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return path
    return None


def _usable_reports(reports: List[dict]) -> List[dict]:
    kept = []
    for index, report in enumerate(reports):
        missing = _missing_path(report or {})
        if missing:
            logger.warning(f"SHC v2 dropped report at index {index} ({(report or {}).get('computedID')}): no {missing}")
            continue
        kept.append(report)
    return kept


def _report_item(report: dict) -> Item:
    farmer = (report.get("farmer") or {}).get("name") or "Unknown"
    html = report.get("html") if isinstance(report.get("html"), str) else ""
    return Item(
        id=report.get("computedID") or report.get("id") or "unknown",
        tags=[],
        descriptor=Descriptor(name=f"Soil Health Card for Farmer {farmer}"),
        media=[MediaResource(mimetype="text/html", url=base64.b64encode(html.encode()).decode())],
    )


async def _cache_report_cards(provider: Provider, phone: str, cycle: str) -> None:
    # Same suffix shape as v1's cache_html_and_replace_urls: response-provider-item-media.
    for index, item in enumerate(provider.items):
        media = item.media[0]
        if media.is_html_report():
            media.ensure_injected(phone, cycle, f"0-0-{index}-0")
            await media.cache_and_update_url()


async def _render_reports(reports: List[dict], phone: str, cycle: str) -> str:
    usable = _usable_reports(reports)
    logger.info(f"SHC v2 got {len(reports)} reports, {len(usable)} usable")
    if not usable:
        return _NO_DATA
    scheme = usable[0].get("scheme") or {}
    provider = Provider(
        id=scheme.get("_id") or "unknown_provider",
        descriptor=Descriptor(name=scheme.get("department") or "Unknown Department", short_desc=_LAB_DESCRIPTION),
        items=[_report_item(r) for r in usable],
    )
    await _cache_report_cards(provider, phone, cycle)
    return str(provider).strip()


@observe(name="tool:check_shc_status", as_type="tool")
async def check_shc_status(
    ctx: RunContext[FarmerContext],
    phone_number: str,
    cycle: str,
) -> str:
    """Check soil health card status.

    Use this tool to check soil health card status for a farmer.

    Args:
        phone_number (str): Phone number registered with the scheme
        cycle (str): Cycle year for which status is requested (e.g., "2023-24", "2024-25"). You must ask the user for the cycle year if not provided. Do not mention the format specification to the user - ask naturally for the cycle year.

    Returns:
        str: Detailed soil health card information
    """
    transaction_id = str(uuid.uuid4())
    try:
        phone = format_phone_number(phone_number)
        lf_update_current_observation(
            metadata={"tool": "shc.status", "beckn_version": "v2", "transaction_id": transaction_id},
        )
        found = await _discover_reports_resource(transaction_id)
        if not found:
            return _UNAVAILABLE
        fields = await _request(found["resource"], phone, cycle)
        on_select = await _post("select", _select_body(found, fields, transaction_id))
        answer = _shc_answer(on_select)
        reports = (answer.get("data") or {}).get("getTestForAuthUser")
        if answer.get("errors") or not isinstance(reports, list):
            logger.error(f"SHC v2 provider returned no report list: errors={[e.get('message') for e in answer.get('errors') or []]}")
            return _UNAVAILABLE
        return await _render_reports(reports, phone_number, cycle)
    except SchemaUnavailable as e:
        logger.error(f"SHC v2 could not read the select request schema: {e}")
        return _UNAVAILABLE
    except httpx.TimeoutException as e:
        logger.error(f"Soil health card status API request timed out: {e}")
        return _TIMED_OUT
    except httpx.HTTPStatusError as e:
        logger.error(f"SHC v2 adapter returned {e.response.status_code}: {e.response.text[:500]}")
        return f"Soil health card status service unavailable. Status code: {e.response.status_code}"
    except httpx.RequestError as e:
        logger.error(f"Soil health card status API request failed: {e}")
        return f"Soil health card status request failed: {str(e)}"
    except Exception as e:
        logger.error(f"Error in soil health card status: {e}")
        raise ModelRetry(f"Unexpected error in soil health card status request. {str(e)}")
