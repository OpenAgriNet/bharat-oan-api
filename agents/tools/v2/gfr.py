"""GFR fertilizer recommendation over Beckn v2: discover the state, select the soil test, select the doses.
Rendering reuses v1's helpers so both versions show the model the same text."""
import json
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
from agents.tools.gfr import (
    GfrRegistryItem,
    checked_recommendation_inputs,
    format_crop_registry,
    format_recommendations,
)
from agents.tools.v2.provider_openapi import SchemaUnavailable, openapi_document, request_schema
from agents.tools.v2.sathi_seed import AGRICULTURE_RESOURCE_CONTEXT
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)

# Branch URL until the AgricultureAdvisory pack is published, as for AgricultureResource.
AGRICULTURE_ADVISORY_CONTEXT = (
    "https://raw.githubusercontent.com/OpenAgriNet/network-specs/bv-features/"
    "schema/AgricultureAdvisory/v0.2/context.jsonld"
)
_RESOURCE_TYPE = "openagrinet:AgricultureResource"
_ADVISORY_TYPE = "openagrinet:AgricultureAdvisory"
# The advice is one AgricultureAdvisory per state; the soil test is one AgricultureResource.
_ADVICE = {"code": "gfr-advice", "type": _ADVISORY_TYPE, "context": AGRICULTURE_ADVISORY_CONTEXT}
_SOIL = {"code": "gfr-soil-report", "type": _RESOURCE_TYPE, "context": AGRICULTURE_RESOURCE_CONTEXT}
_SHC_CODE_SCHEME = "SHC"
_SOIL_VALUES = ("n", "p", "k", "OC")

# The values v1's provider-backend sent for the soil test.
_SOIL_TEST_LIMIT = 1
_SOIL_TEST_SKIP = 0
_SOIL_TEST_LOCALE = "en"

_NO_DATA = "No recommendation data found."
_TIMED_OUT = "GFR network request timed out. Please try again."
_UNAVAILABLE = "GFR service unavailable right now. Please try again."
_INVALID = "GFR service returned an invalid response."


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
        # The GFR catalog is public, so discover must ask the "*" audience.
        "networkId": os.getenv("OAN_PUBLIC_NETWORK_ID", "*") if action == "discover" else network_id,
        "senderId": network_id,
        "receiverId": network_id,
        "transactionId": transaction_id,
        "messageId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        # Discovery returns only resources of the packs named here, so both are named.
        "schemaContext": [f"{kind['context']}#{kind['type']}" for kind in (_SOIL, _ADVICE)],
    }
    if action != "discover":
        context["bppUri"] = f"https://{network_id}"
    return context


async def _post(action: str, body: dict) -> dict:
    """One adapter call; failures become v1's ModelRetry texts."""
    url = _adapter_url(action)
    logger.info(f"GFR v2 {action} -> {url}")
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(url, json=body)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException:
        logger.error(f"GFR v2 {action} timed out")
        raise ModelRetry(_TIMED_OUT)
    except httpx.HTTPStatusError as e:
        logger.error(f"GFR v2 {action}: adapter returned {e.response.status_code}: {e.response.text[:500]}")
        raise ModelRetry(_UNAVAILABLE)
    except httpx.RequestError as e:
        logger.error(f"GFR v2 {action} failed: {e}")
        raise ModelRetry(f"GFR network request failed. {str(e)}")
    except ValueError:
        logger.error(f"GFR v2 {action}: the adapter's answer is not JSON")
        raise ModelRetry(_INVALID)


def _discover_body(transaction_id: str, condition: str, point: Optional[List[float]] = None) -> dict:
    intent: Dict[str, Any] = {
        "filters": {"type": "jsonpath", "expression": f"$.catalogs[*].resources[*] ? ({condition})"}
    }
    if point:
        intent["spatial"] = [{
            "op": "S_INTERSECTS",
            "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
            # GeoJSON is [longitude, latitude].
            "geometry": {"type": "Point", "coordinates": point},
            "quantifier": "ANY",
        }]
    return {"context": _context("discover", transaction_id), "message": {"intent": intent}}


async def _discover(transaction_id: str, condition: str, point: Optional[List[float]] = None) -> List[dict]:
    """Every GFR resource matching, each with its catalog provider."""
    payload = await _post("discover", _discover_body(transaction_id, condition, point))
    found = [
        {"resource": resource, "provider": catalog.get("provider") or {}}
        for catalog in (payload.get("message") or {}).get("catalogs") or []
        for resource in catalog.get("resources") or []
    ]
    logger.info(f"GFR v2 discover matched {[f['resource'].get('id') for f in found]}")
    return found


def _of_kind(found: List[dict], kind: dict) -> List[dict]:
    """The resources of one kind. A stale resource with the right code but another pack is skipped."""
    kept = []
    for entry in found:
        resource = entry["resource"]
        if (resource.get("descriptor") or {}).get("code") != kind["code"]:
            continue
        if (resource.get("resourceAttributes") or {}).get("@type") != kind["type"]:
            logger.info(f"GFR v2 skipped {resource.get('id')}: {kind['code']} but not {kind['type']}")
            continue
        kept.append(entry)
    return kept


def _shc_state_id(resource: dict) -> str:
    for area in (resource.get("resourceAttributes") or {}).get("coverageAreas") or []:
        if area.get("codeScheme") == _SHC_CODE_SCHEME:
            return area.get("areaCode") or ""
    return ""


def _registry_items(resource: dict) -> List[GfrRegistryItem]:
    """The state's crops in v1's registry-row shape, so v1's formatting applies unchanged."""
    state_id = _shc_state_id(resource)
    items = []
    for subject in (resource.get("resourceAttributes") or {}).get("agricultureSubjects") or []:
        descriptor = subject.get("descriptor") or {}
        items.append(GfrRegistryItem.model_validate({
            "id": descriptor.get("code"),
            "descriptor": {"name": descriptor.get("name"), "long_desc": descriptor.get("name")},
            # The catalog lists only crops GFR covers.
            "tags": [{"descriptor": {"code": "crop_details"}, "list": [
                {"descriptor": {"code": "GFRavailable"}, "value": "Yes"},
                {"descriptor": {"code": "stateId"}, "value": state_id},
            ]}],
        }))
    return items


async def _query_for(resource: dict, action: str) -> str:
    """The provider's GraphQL query for one action, the const its OpenAPI fixes."""
    for entry in (resource.get("resourceAttributes") or {}).get("actions") or []:
        if entry.get("action") == action and entry.get("operationRef"):
            url, _, pointer = entry["operationRef"].partition("#")
            query = (request_schema(await openapi_document(url), pointer)["properties"].get("query") or {}).get("const")
            if query:
                return query
            raise SchemaUnavailable(f"{url}: the {action} request has no const query")
    raise SchemaUnavailable(f"{resource.get('id')} has no {action} operationRef")


def _select_body(found: dict, kind: dict, fields: dict, transaction_id: str) -> dict:
    resource_id = found["resource"].get("id")
    return {
        "context": _context("select", transaction_id),
        "message": {
            "contract": {
                "status": {"code": "ACTIVE", "name": "active"},
                "commitments": [{
                    "status": {"descriptor": {"code": "ACTIVE", "name": "active"}},
                    "resources": [{
                        "id": resource_id,
                        "quantity": {"count": 1},
                        # The adapter adds the soil test's login; it is never sent from here.
                        "resourceAttributes": {"@context": kind["context"], "@type": kind["type"], **fields},
                    }],
                    "offer": {"id": f"offer:{kind['code']}", "resourceIds": [resource_id], "provider": found["provider"]},
                }],
            }
        },
    }


def _answer_attributes(answer: dict) -> dict:
    commitments = ((answer.get("message") or {}).get("contract") or {}).get("commitments") or [{}]
    resources = commitments[0].get("resources") or [{}]
    return resources[0].get("resourceAttributes") or {}


def _as_v1_item(recommendations: Any) -> dict:
    """The item provider-backend built around the raw answer, so v1's formatting applies unchanged."""
    return {
        "id": "gfr-recommendation",
        "descriptor": {"name": "Crop Recommendation"},
        "tags": [{"descriptor": {"code": "recommendations"},
                  "list": [{"descriptor": {"code": "data"}, "value": json.dumps(recommendations)}]}],
    }


async def _soil_values(found: dict, phone: str, cycle: str, transaction_id: str) -> Optional[dict]:
    """n, p, k and OC from the farmer's first soil report, or None when there is none."""
    variables = {"phone": phone, "cycle": cycle, "limit": _SOIL_TEST_LIMIT, "skip": _SOIL_TEST_SKIP, "locale": _SOIL_TEST_LOCALE}
    query = await _query_for(found["resource"], "select")
    attributes = _answer_attributes(await _post("select", _select_body(
        found, _SOIL, {"query": query, "variables": variables}, transaction_id)))
    if attributes.get("errors"):
        logger.error(f"GFR v2 soil test refused: {[e.get('message') for e in attributes['errors']]}")
        return None
    results = attributes.get("results")
    if not isinstance(results, dict):
        logger.info(f"GFR v2 no soil test for cycle {cycle}")
        return None
    return {name: results.get(name) for name in _SOIL_VALUES}


async def _advice_and_soil_report(state_id: str, transaction_id: str) -> Optional[tuple]:
    """The state's advice resource and the soil-report resource, or None when either is missing."""
    found = await _discover(transaction_id, (
        f'(@.descriptor.code == "{_ADVICE["code"]}" && @.resourceAttributes.coverageAreas[*].areaCode == "{state_id}")'
        f' || @.descriptor.code == "{_SOIL["code"]}"'))
    advice, soil_report = _of_kind(found, _ADVICE), _of_kind(found, _SOIL)
    if not advice or not soil_report:
        logger.warning(f"GFR v2 state {state_id}: advice found {bool(advice)}, soil report found {bool(soil_report)}")
        return None
    return advice[0], soil_report[0]


async def _doses(advice: dict, variables: dict, transaction_id: str) -> str:
    """The recommendation text for these soil values and crops, in v1's words."""
    query = await _query_for(advice["resource"], "select")
    logger.info(f"GFR v2 selecting {advice['resource'].get('id')} for crops {variables['crops']}")
    attributes = _answer_attributes(await _post("select", _select_body(
        advice, _ADVICE, {"query": query, "variables": variables}, transaction_id)))
    recommendations = (attributes.get("data") or {}).get("getRecommendations")
    if recommendations is None:
        logger.warning(f"GFR v2 no recommendation: errors={[e.get('message') for e in attributes.get('errors') or []]}")
        return _NO_DATA
    return format_recommendations([_as_v1_item(recommendations)])


@observe(name="tool:gfr_get_crop_registries", as_type="tool")
async def gfr_get_crop_registries(
    ctx: RunContext[FarmerContext],
    latitude: float,
    longitude: float,
    only_gfr_available: bool = True,
    crop_name_contains: Optional[str] = None,
    limit: int = 25,
) -> str:
    """Crop registry from BAP /search (location lat/lon)."""
    transaction_id = str(uuid.uuid4())
    lf_update_current_observation(
        metadata={"tool": "gfr.crop_registries", "beckn_version": "v2", "transaction_id": transaction_id})
    found = await _discover(transaction_id, f'@.descriptor.code == "{_ADVICE["code"]}"', point=[longitude, latitude])
    crops = [item for state in _of_kind(found, _ADVICE) for item in _registry_items(state["resource"])]
    return format_crop_registry(crops, only_gfr_available, crop_name_contains, limit)


@observe(name="tool:gfr_get_recommendations", as_type="tool")
async def gfr_get_recommendations(
    ctx: RunContext[FarmerContext],
    state_id: str,
    crops: List[str],
    phone_no: str,
    cycle: str,
    district_id: Optional[str] = None,
    natural_farming: bool = False,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> str:
    """
    Fertilizer recommendation from BAP /search. Pass stateId, crop ids (from registry), phone
    (10-digit Indian number or with 91 / +91), SHC cycle year; optional
    districtId and lat/lon. Soil values come from the network (SHC). Default natural_farming=False
    matches sandbox /search (chemical + organic quantity tables); True returns NF practice advisory text.
    """
    state_id, crop_ids, phone, cycle_y = checked_recommendation_inputs(state_id, crops, phone_no, cycle)
    transaction_id = str(uuid.uuid4())
    lf_update_current_observation(
        metadata={"tool": "gfr.recommendations", "beckn_version": "v2", "transaction_id": transaction_id})
    # v1 sent district and lat/lon too, and provider-backend dropped them; v2 does not send them.
    resources = await _advice_and_soil_report(state_id, transaction_id)
    if resources is None:
        return _NO_DATA
    advice, soil_report = resources
    try:
        soil = await _soil_values(soil_report, phone, cycle_y, transaction_id)
        if soil is None:
            return _NO_DATA
        variables = {"state": state_id, "results": soil, "crops": crop_ids, "naturalFarming": bool(natural_farming)}
        return await _doses(advice, variables, transaction_id)
    except SchemaUnavailable as e:
        logger.error(f"GFR v2 could not read the request schema: {e}")
        raise ModelRetry(_UNAVAILABLE)
