"""SATHI seed availability over Beckn v2: discover the district, select its stock.
Rendering reuses v1's helpers so both versions answer with the same text."""
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from langfuse import observe
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.sathi_seed import (
    MAX_SATHI_DEALERS_IN_SEARCH,
    _accumulate_dealer,
    _format_dealer_details,
    _num,
    _seed_availability_data_not_available_response,
)
from agents.tools.v2.provider_openapi import (
    FieldSpec,
    From,
    SchemaUnavailable,
    request_fields,
    select_fields,
)
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)

# Named as published. OAN_SCHEMA_BASE moves the fetch for a stack that cannot reach the site.
AGRICULTURE_RESOURCE_CONTEXT = (
    os.getenv("OAN_SCHEMA_BASE", "https://openagrinet.github.io/network-specs/schema").rstrip("/")
    + "/AgricultureResource/v0.2/context.jsonld"
)
_SCHEMA_TYPE = "openagrinet:AgricultureResource"
# Every SATHI district resource in the catalog carries this descriptor code.
_SATHI_STOCK_CODE = "sathi-stock"

# discover returns no offer; this is the id the working select carried.
_OFFER_ID = "offer:sathi-stock"
_SATHI_OK_STATUS = 200

# Word-for-word from v1 and its skill prompt.
_OUTSIDE_COVERAGE = (
    "SATHI seed information is currently available only for Maharashtra. "
    "Would you like to check a district in Maharashtra instead?"
)
_SERVICE_ERROR = "Seed availability service returned an error. Please try again later."
_TIMED_OUT = "Seed availability request timed out. Please try again later."
_UNREACHABLE = "Could not reach seed availability service. Please try again later."
_UNEXPECTED = "Unexpected error while fetching seed availability. Please try again later."


# Which coverage area answers a request field, by what the contract says the
# field is for. SATHI's own field names are never written here: the contract
# names them, and this says only where a state and a district come from.
_AREA_FOR = {From.STATE: "State", From.DISTRICT: "District"}

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


async def _field_specs(resource: dict) -> Dict[str, FieldSpec]:
    return await request_fields(resource, "select")


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
        "networkId": network_id,
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
    logger.info(f"SATHI seed v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json()


def _discover_body(latitude: float, longitude: float, transaction_id: str) -> dict:
    return {
        "context": _context("discover", transaction_id),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        f'@.descriptor.code == "{_SATHI_STOCK_CODE}")'
                    ),
                },
                "spatial": [
                    {
                        "op": "S_INTERSECTS",
                        "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
                        # GeoJSON is [longitude, latitude].
                        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                        "quantifier": "ANY",
                    }
                ],
            }
        },
    }


async def _discover_districts(
    latitude: float, longitude: float, transaction_id: str
) -> List[Dict[str, Any]]:
    """Every SATHI district resource covering the point, each with its catalog provider."""
    payload = await _post("discover", _discover_body(latitude, longitude, transaction_id))
    found = []
    for catalog in payload.get("message", {}).get("catalogs", []) or []:
        provider = catalog.get("provider") or {}
        for resource in catalog.get("resources", []) or []:
            found.append({"resource": resource, "provider": provider})
    logger.info(
        f"SATHI seed v2 discover at {latitude},{longitude} matched "
        f"{[f['resource'].get('id') for f in found]}"
    )
    return found


def _area(resource: dict, level: str) -> dict:
    for area in (resource.get("resourceAttributes") or {}).get("coverageAreas") or []:
        if area.get("areaLevel") == level:
            return area
    return {}


def _location_label(resource: dict) -> str:
    names = (_area(resource, "District").get("areaName"), _area(resource, "State").get("areaName"))
    return ", ".join(n for n in names if n)


def _crops(resource: dict) -> List[Dict[str, str]]:
    subjects = (resource.get("resourceAttributes") or {}).get("agricultureSubjects") or []
    return [s.get("descriptor") or {} for s in subjects if (s.get("descriptor") or {}).get("code")]


def _ask_which_district(found: List[Dict[str, Any]]) -> str:
    names = ", ".join(_area(f["resource"], "District").get("areaName") or "" for f in found)
    return (
        f"This location falls in more than one SATHI district: {names}. "
        "Ask the farmer which of these districts they are in, then call this tool again with "
        "the same latitude and longitude and district set to the chosen name."
    )


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _district_named(found: List[Dict[str, Any]], district: Optional[str]) -> Optional[Dict[str, Any]]:
    """The one candidate whose district name matches, or None."""
    names = [_area(f["resource"], "District").get("areaName") for f in found]
    matches = [f for f in found if _same_name(_area(f["resource"], "District").get("areaName"), district)]
    if len(matches) != 1:
        logger.info(f"SATHI seed v2 district {district!r} matched {len(matches)} of candidates {names}")
        return None
    logger.info(f"SATHI seed v2 chose {matches[0]['resource'].get('id')} for district {district!r} from {names}")
    return matches[0]


def _warn_if_district_disagrees(found: Dict[str, Any], district: Optional[str]) -> None:
    discovered = _area(found["resource"], "District").get("areaName")
    if district and not _same_name(discovered, district):
        logger.info(
            f"SATHI seed v2 ignoring district {district!r}: discover found only {discovered!r}, using that"
        )


def _single_district_or_reply(
    found: List[Dict[str, Any]], latitude: float, longitude: float, district: Optional[str]
):
    """The one district resource, or the text to return when there is not exactly one."""
    if not found:
        logger.info(f"SATHI seed v2 found no district at {latitude},{longitude}; outside SATHI coverage")
        return None, _OUTSIDE_COVERAGE
    if len(found) == 1:
        _warn_if_district_disagrees(found[0], district)
        return found[0], None
    chosen = _district_named(found, district) if district else None
    if chosen:
        return chosen, None
    logger.info(
        f"SATHI seed v2 point {latitude},{longitude} is in {len(found)} districts "
        f"{[f['resource'].get('id') for f in found]}; asking the farmer which one"
    )
    return None, _ask_which_district(found)


def _crop_list_text(resource: dict) -> str:
    district = _area(resource, "District").get("areaName") or ""
    state = _area(resource, "State").get("areaName") or ""
    lines = [
        f"SATHI crops for district {district}, state {state}.",
        "Pick the crop_code for search_sathi_seed_availability yourself; "
        "never show crop codes to the farmer, only crop names.",
        "",
    ]
    lines += [f"{crop['code']}: {crop.get('name') or ''}" for crop in _crops(resource)]
    return "\n".join(lines)


def _what_to_put(name: str, spec: FieldSpec, resource: dict) -> str:
    """What to tell the model to put in one request field, from the contract's
    own hint about what the field is for."""
    if spec.hint == From.CROP_CODE:
        return "the code from the crop list above for the farmer's crop"
    area_kind = _AREA_FOR.get(spec.hint)
    if area_kind:
        area = _area(resource, area_kind)
        return f"{area.get('areaCode')} ({area.get('areaName')})"
    logger.info(f"SATHI seed v2 has no hint for request field {name}; listing its type only")
    return f"a value of type {' or '.join(spec.types) or 'any'}"


def _request_fields_text(resource: dict, specs: Dict[str, FieldSpec]) -> str:
    lines = [
        "",
        "Request fields for search_sathi_seed_availability (pass them as fields; use these codes, never names):",
    ]
    for name, spec in specs.items():
        if spec.source == "model":
            lines.append(f"- {name}{' (required)' if spec.required else ''}: {_what_to_put(name, spec, resource)}")
    return "\n".join(lines)


def _has_type(value: Any, types: Tuple[str, ...]) -> bool:
    if not types:
        return True
    if isinstance(value, bool) and "boolean" not in types:
        return False
    return any(isinstance(value, _JSON_TYPES[t]) for t in types if t in _JSON_TYPES)


def _checked_crop(name: str, value: Any, resource: dict):
    codes = {str(c["code"]).strip().upper(): c["code"] for c in _crops(resource)}
    code = codes.get(str(value).strip().upper())
    if code is None:
        logger.info(f"SATHI seed v2 refused {name} {value!r}: not in the crop list of {resource.get('id')}")
        return None, (
            f"{name} {value} is not in the SATHI crop list for {_location_label(resource)}; "
            "pick a code from list_sathi_crops"
        )
    return code, None


def _checked_area_code(name: str, value: Any, area: dict):
    code, area_name = str(area.get("areaCode") or ""), area.get("areaName")
    text = str(value).strip()
    if code and text == code:
        return area["areaCode"], None
    if code and _same_name(text, area_name):
        logger.info(f"SATHI seed v2 corrected {name} {value!r} to code {code} ({area_name})")
        return area["areaCode"], None
    logger.info(f"SATHI seed v2 refused {name} {value!r}: the district resource has {code} ({area_name})")
    return None, f"{name} must be {code} ({area_name}), not {value}"


def _checked_value(name: str, value: Any, spec: FieldSpec, resource: dict):
    """The value to send, or a problem to tell the model about."""
    if not _has_type(value, spec.types):
        logger.info(f"SATHI seed v2 refused {name} {value!r}: expected {spec.types}")
        return None, f"{name} must be {' or '.join(spec.types)}, not {type(value).__name__}"
    if spec.hint == From.CROP_CODE:
        return _checked_crop(name, value, resource)
    area_kind = _AREA_FOR.get(spec.hint)
    if area_kind:
        return _checked_area_code(name, value, _area(resource, area_kind))
    return value, None


def _log_if_const_overridden(name: str, given: dict, spec: FieldSpec) -> None:
    if name in given and given[name] != spec.value:
        logger.info(f"SATHI seed v2 replaced model value {given[name]!r} for {name} with the fixed {spec.value!r}")


def _prepare_request(given: dict, specs: Dict[str, FieldSpec], resource: dict):
    """The request fields to send and any problems that stop the search."""
    request, problems = {}, []
    for name, value in given.items():
        spec = specs.get(name)
        if spec is None:
            logger.info(f"SATHI seed v2 refused unknown request field {name}")
            problems.append(f"{name} is not a request field")
        elif spec.source == "adapter":
            logger.info(f"SATHI seed v2 dropped {name} sent by the model: the adapter fills it")
        elif spec.source == "model":
            checked, problem = _checked_value(name, value, spec, resource)
            if problem:
                problems.append(problem)
            else:
                request[name] = checked
    for name, spec in specs.items():
        if spec.source == "const":
            _log_if_const_overridden(name, given, spec)
            request[name] = spec.value
        elif spec.source == "model" and spec.required and name not in given:
            problems.append(f"{name} is missing")
    return {name: request[name] for name in specs if name in request}, problems


def _fix_fields_text(problems: List[str]) -> str:
    return (
        "The request fields need fixing before searching: " + "; ".join(problems) + ". "
        "Use the request fields and codes listed by list_sathi_crops."
    )


def _crop_code(request: dict, specs: Dict[str, FieldSpec]) -> str:
    """The crop the search was for, found by what the contract says each field
    is for rather than by SATHI's name for it."""
    return next(
        (str(request[name]) for name, spec in specs.items()
         if spec.hint == From.CROP_CODE and name in request),
        "",
    )


def _select_body(found: Dict[str, Any], request_fields: dict, transaction_id: str) -> dict:
    resource = found["resource"]
    resource_id = resource.get("id")
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
                                # The adapter adds SATHI's API key; it is never sent from here.
                                "resourceAttributes": {
                                    "@context": AGRICULTURE_RESOURCE_CONTEXT,
                                    "@type": _SCHEMA_TYPE,
                                    **request_fields,
                                },
                            }
                        ],
                        "offer": {
                            "id": _OFFER_ID,
                            "resourceIds": [resource_id],
                            "provider": found["provider"],
                        },
                    }
                ],
            }
        },
    }


def _sathi_answer(on_select: dict) -> dict:
    """SATHI's own response fields, carried in the first resource of the first commitment."""
    commitments = ((on_select.get("message") or {}).get("contract") or {}).get("commitments") or [{}]
    resources = commitments[0].get("resources") or [{}]
    return resources[0].get("resourceAttributes") or {}


def _accumulate_lots(lots: List[dict]) -> Dict[str, Dict[str, Any]]:
    dealer_acc: Dict[str, Dict[str, Any]] = {}
    skipped_rows = lots_with_skips = 0
    for lot in lots:
        variety = str(lot.get("variety_name") or "").strip() or "(unknown variety)"
        rows = lot.get("available_at") or []
        empty = [r.get("dealer_id") for r in rows if _num(r.get("bags"), True) <= 0]
        if empty:
            skipped_rows += len(empty)
            lots_with_skips += 1
            logger.debug(f"SATHI seed v2 lot {lot.get('lot_number')} ({variety}): skipped dealers {empty}")
        for row in rows:
            _accumulate_dealer(dealer_acc, {"value": row}, variety)
    if skipped_rows:
        # One line per search: a large district has hundreds of lots, and one line each drowns the log.
        logger.info(
            f"SATHI seed v2 skipped {skipped_rows} dealer rows with no bags in stock, "
            f"across {lots_with_skips} of {len(lots)} lots"
        )
    return dealer_acc


def _render_answer(answer: dict, location: str, crop_code: str) -> str:
    status = _num(answer.get("statusCode"), True)
    lots = answer.get("data") if isinstance(answer.get("data"), list) else []
    if status != _SATHI_OK_STATUS or not lots:
        logger.warning(
            f"SATHI seed v2 no data for crop {crop_code} in {location}: "
            f"statusCode={answer.get('statusCode')} message={answer.get('message')!r} lots={len(lots)}"
        )
        return _seed_availability_data_not_available_response(location)

    dealer_acc = _accumulate_lots(lots)
    if not dealer_acc:
        logger.info(f"SATHI seed v2 crop {crop_code} in {location}: {len(lots)} lots but no dealer with stock")
        return (
            f"No dealers with stock found for this crop in {location}, crop {crop_code}.\n\n"
            "**Source: SATHI**"
        )
    return _format_dealer_details(
        dealer_acc, location, include_source_footer=True, max_dealers=MAX_SATHI_DEALERS_IN_SEARCH
    )


def _transport_failure_text(error: Exception) -> str:
    if isinstance(error, SchemaUnavailable):
        logger.error(f"SATHI seed v2 could not read the select request schema: {error}")
        return _SERVICE_ERROR
    if isinstance(error, httpx.TimeoutException):
        logger.error("SATHI seed v2 request timed out")
        return _TIMED_OUT
    if isinstance(error, httpx.HTTPStatusError):
        logger.error(
            f"SATHI seed v2 adapter returned {error.response.status_code}: {error.response.text[:500]}"
        )
        return _SERVICE_ERROR
    if isinstance(error, httpx.RequestError):
        logger.error(f"SATHI seed v2 request failed: {error}")
        return _UNREACHABLE
    logger.exception("SATHI seed v2 failed unexpectedly")
    return _UNEXPECTED


@observe(name="tool:list_sathi_crops", as_type="tool")
async def list_sathi_crops(
    ctx: RunContext[FarmerContext],
    latitude: float,
    longitude: float,
    district: Optional[str] = None,
) -> str:
    """SATHI crops for the district at ``latitude``, ``longitude``; pick crop_code for search_sathi_seed_availability.

    Pass ``district`` only when this tool asked which district the farmer is in.
    """
    transaction_id = str(uuid.uuid4())
    try:
        lf_update_current_observation(
            input={"latitude": latitude, "longitude": longitude, "district": district},
            metadata={"tool": "sathi.crop_list", "beckn_version": "v2", "transaction_id": transaction_id},
        )
        found = await _discover_districts(latitude, longitude, transaction_id)
        district, reply = _single_district_or_reply(found, latitude, longitude, district)
        if reply:
            return reply
        resource = district["resource"]
        return _crop_list_text(resource) + "\n" + _request_fields_text(resource, await _field_specs(resource))
    except Exception as e:
        return _transport_failure_text(e)


@observe(name="tool:search_sathi_seed_availability", as_type="tool")
async def search_sathi_seed_availability(
    ctx: RunContext[FarmerContext],
    fields: Dict[str, Any],
    latitude: float,
    longitude: float,
    district: Optional[str] = None,
) -> str:
    """SATHI certified seed dealers with stock at ``latitude``, ``longitude``.

    ``fields`` are the request fields list_sathi_crops lists for this district, filled with the codes it shows.

    Returns dealer names, district, contact, aggregated bags/quintals, variety labels, and
    **Source: SATHI**. Dealer rows are capped when the catalog is very large.
    Pass ``district`` only when this tool asked which district the farmer is in.
    """
    transaction_id = str(uuid.uuid4())
    try:
        lf_update_current_observation(
            input={"fields": fields, "latitude": latitude, "longitude": longitude, "district": district},
            metadata={"tool": "sathi.seed_availability", "beckn_version": "v2", "transaction_id": transaction_id},
        )
        found = await _discover_districts(latitude, longitude, transaction_id)
        district, reply = _single_district_or_reply(found, latitude, longitude, district)
        if reply:
            return reply

        resource = district["resource"]
        specs = await _field_specs(resource)
        request, problems = _prepare_request(fields or {}, specs, resource)
        if problems:
            logger.info(f"SATHI seed v2 sent no select for {resource.get('id')}: {problems}")
            return _fix_fields_text(problems)

        logger.info(f"SATHI seed v2 selecting {resource.get('id')} with fields {sorted(request)}")
        on_select = await _post("select", _select_body(district, request, transaction_id))
        return _render_answer(_sathi_answer(on_select), _location_label(resource), _crop_code(request, specs))
    except Exception as e:
        return _transport_failure_text(e)
