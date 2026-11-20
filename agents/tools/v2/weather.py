"""Weather forecast over Beckn v2.

Same tool and arguments as agents.tools.weather; only the transport changes.
v1 posted one /search to the BAP; v2 splits that into discover (which provider
covers this point) and select (the forecast itself). Both end at the same IMD
Mausamgram NWP upstream.

Renders the v2 shape directly rather than reusing v1's renderer -- the select
mapping drops fields the v0.1 schema pack cannot hold, so the two outputs
cannot be made identical. Rationale, open items and the v1 comparison that is
still outstanding: migration-plan/weather/plan.md.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from langfuse import observe
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.imd_advisory import agromet_note
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, get_today_date_str

load_dotenv()

logger = get_logger(__name__)

_SCHEMA_CONTEXT = (
    "https://openagrinet.github.io/network-specs/schema/WeatherObservation/v0.1/context.jsonld"
)
_SCHEMA_TYPE = "openagrinet:WeatherObservation"

# discover returns no offer; this is the id the worked select example carries.
_OFFER_ID = "offer:weather-observation:mausamgram:open-data"

# Provider failed, however it failed. Not _NO_DATA -- that claims something
# about the location, and a failed request does not.
_UNAVAILABLE = "Weather service unavailable. Please try again later."

# Word-for-word from v1; the skill prompt is written against this sentence.
_NO_DATA = "No weather forecast data found for the requested location."

# Reading order. Anything not listed still renders, it just sorts last.
_PARAMETER_ORDER = [
    "Rainfall",
    "Temperature",
    "Humidity",
    "WindSpeed",
    "WindDirection",
    "Alert",
]

# How the mapping's grouped `values` object reads back as a sentence.
_STATISTIC_LABEL = {
    "instantaneous": "",
    "minimum": "min",
    "maximum": "max",
    "mean": "avg",
    "sum": "",
}

# Which catalog audience to discover in -- not who we are, senderId is that.
# visible_to is matched by array overlap, and a public catalog holds the literal
# "*", so "*" is what finds it. Mausamgram's catalog is public; mandi's is
# scoped to oan.dev. The spec allows an array here to name both at once, but
# the adapter currently refuses it (see plan.md).
_DISCOVER_NETWORK_ID = os.getenv("OAN_PUBLIC_NETWORK_ID", "*")


def _adapter_url(action: str) -> str:
    base = os.getenv("OAN_ADAPTER_URL", "http://network-adapter:9201/consumer")
    return base.rstrip("/") + "/" + action


def _context(action: str) -> dict:
    """The v2 envelope. Single-network pilot, so sender and receiver match."""
    network_id = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return {
        "domain": os.getenv("OAN_DOMAIN", "agriculture"),
        "action": action,
        "version": "2.0.0",
        # select is routed by binding key and signed as this node, so only
        # discover takes the audience selector.
        "networkId": _DISCOVER_NETWORK_ID if action == "discover" else network_id,
        "senderId": network_id,
        "receiverId": network_id,
        "transactionId": str(uuid.uuid4()),
        "messageId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "schemaContext": [f"{_SCHEMA_CONTEXT}#{_SCHEMA_TYPE}"],
    }


async def _post(action: str, body: dict) -> dict:
    url = _adapter_url(action)
    logger.info(f"Weather v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json()


async def _discover_provider(
    latitude: float, longitude: float
) -> Optional[Dict[str, Any]]:
    """The forecast resource covering this point, or None.

    Nothing to rank, unlike mandi: Mausamgram publishes one resource for its
    whole 0.25 degree grid. Note the coverage area is a bounding box
    (68-97.5E, 6.5-37.5N), not India's outline, so a sea point discovers fine.
    """
    body = {
        "context": _context("discover"),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        '@.resourceAttributes.subjectCategories[*] == "Weather" && '
                        '@.resourceAttributes.informationMode == "OnDemand")'
                    ),
                },
                "spatial": [
                    {
                        "op": "S_INTERSECTS",
                        "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
                        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                        "quantifier": "ANY",
                    }
                ],
            }
        },
    }
    payload = await _post("discover", body)
    return _first_resource(payload)


def _first_resource(payload: dict) -> Optional[Dict[str, Any]]:
    for catalog in payload.get("message", {}).get("catalogs", []) or []:
        provider = catalog.get("provider") or {}
        for resource in catalog.get("resources", []) or []:
            return {"resource": resource, "provider": provider}
    return None


async def _select_forecast(
    found: Dict[str, Any], latitude: float, longitude: float
) -> List[dict]:
    """Invoke the resource for this point; returns one Direct resource per day.

    No date range is sent: the upstream takes lat and lon only and always
    answers its full horizon.
    """
    resource = found["resource"]
    attrs = dict(resource.get("resourceAttributes") or {})

    # Built explicitly rather than copied from discover -- the mapping's
    # precondition requires this location and refuses anything else.
    request_attrs = {
        "@context": _SCHEMA_CONTEXT,
        "@type": _SCHEMA_TYPE,
        "informationMode": "OnDemand",
        "subjectCategories": attrs.get("subjectCategories") or ["Weather"],
        "supportedObservationTypes": attrs.get("supportedObservationTypes") or ["Forecast"],
        "supportedParameters": attrs.get("supportedParameters") or list(_PARAMETER_ORDER),
        "geographicGranularities": attrs.get("geographicGranularities") or ["Point"],
        # GeoJSON is [longitude, latitude]; swapped, this is a valid request
        # for the wrong place rather than an error.
        "location": {"geo": {"type": "Point", "coordinates": [longitude, latitude]}},
    }

    body = {
        "context": _context("select"),
        "message": {
            "contract": {
                "status": {"code": "ACTIVE", "name": "active"},
                "commitments": [
                    {
                        "status": {"descriptor": {"code": "ACTIVE", "name": "active"}},
                        "resources": [
                            {
                                "id": resource.get("id"),
                                "quantity": {"count": 1},
                                "resourceAttributes": request_attrs,
                            }
                        ],
                        "offer": {
                            "id": _OFFER_ID,
                            "resourceIds": [resource.get("id")],
                            "provider": found["provider"],
                        },
                    }
                ],
            }
        },
    }
    payload = await _post("select", body)

    days: List[dict] = []
    for commitment in payload.get("message", {}).get("contract", {}).get("commitments", []) or []:
        for res in commitment.get("resources", []) or []:
            res_attrs = res.get("resourceAttributes") or {}
            # Only Direct carries readings; OnDemand back means the provider
            # echoed the catalog entry instead of answering.
            if res_attrs.get("informationMode") == "Direct" and res_attrs.get("parameters"):
                days.append(res)
    return days


def _day_label(resource: dict) -> str:
    """The forecast day as DD-MM-YYYY, off validity.startsAt (fixed IST)."""
    attrs = resource.get("resourceAttributes") or {}
    starts_at = ((attrs.get("validity") or {}).get("startsAt") or "")[:10]
    if not starts_at:
        return "Unknown date"
    try:
        return datetime.strptime(starts_at, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return starts_at


def _render_parameter(entry: dict) -> Optional[str]:
    """One parameter as a line: "Temperature: min 21.4, max 33.8 Cel"."""
    name = entry.get("parameter") or ""
    values = entry.get("values") or {}
    unit = entry.get("unit") or ""
    if not name or not values:
        return None

    # Alert is IMD's own condition text; no unit, because "Overcast Sky 1"
    # is not a sentence.
    if name == "Alert":
        text = values.get("instantaneous")
        return f"{name}: {text}" if text else None

    parts = []
    for key, value in values.items():
        if value is None:
            continue
        label = _STATISTIC_LABEL.get(key, key)
        parts.append(f"{label} {value}".strip())
    if not parts:
        return None

    suffix = f" {unit}" if unit and unit != "1" else ""
    return f"{name}: {', '.join(parts)}{suffix}"


def _parameter_sort_key(entry: dict) -> Tuple[int, str]:
    """Known parameters in reading order; anything new sorts last but renders."""
    name = entry.get("parameter") or ""
    if name in _PARAMETER_ORDER:
        return (_PARAMETER_ORDER.index(name), "")
    return (len(_PARAMETER_ORDER), name)


def _render(days: List[dict]) -> str:
    """The v2 answer as text. Heading and no-data sentence are v1's exactly."""
    lines = [f"**Weather Forecast Data** [Today's Date: {get_today_date_str()}]"]
    if not days:
        lines.append(_NO_DATA)
        return "\n".join(lines)

    first = (days[0].get("resourceAttributes") or {})
    source = (first.get("source") or {}).get("sourceName") or "IMD Mausamgram NWP"
    coords = ((first.get("location") or {}).get("geo") or {}).get("coordinates") or []
    if len(coords) >= 2:
        # The grid cell, up to ~14 km from the point asked for. Stated so two
        # nearby villages getting identical numbers makes sense.
        lines.append(f"Forecast point: {coords[1]}, {coords[0]} (nearest grid centre)")

    for resource in days:
        attrs = resource.get("resourceAttributes") or {}
        readings = [
            rendered
            for rendered in (
                _render_parameter(entry)
                for entry in sorted(attrs.get("parameters") or [], key=_parameter_sort_key)
            )
            if rendered
        ]
        if not readings:
            continue
        lines.append(f"\n{_day_label(resource)}:")
        lines.extend(f"  {reading}" for reading in readings)

    lines.append(f"\nSource: {source}")
    return "\n".join(lines)


@observe(name="tool:weather_forecast", as_type="tool")
async def weather_forecast(
    ctx: RunContext[FarmerContext],
    latitude: float,
    longitude: float,
) -> str:
    """Get Weather forecast for a specific location.

    Args:
        latitude (float): Latitude of the location
        longitude (float): Longitude of the location

    Returns:
        str: The weather forecast for the specific location
    """
    try:
        lf_update_current_observation(
            input={"latitude": latitude, "longitude": longitude},
            metadata={"tool": "weather.forecast", "beckn_version": "v2"},
        )

        found = await _discover_provider(latitude, longitude)
        if not found:
            logger.info(f"Weather v2 discover found no provider for {latitude},{longitude}")
            # Appended even with no forecast, as v1 did: it answers a different
            # question and does not depend on this one.
            return _render([]) + await agromet_note(ctx, latitude, longitude)

        days = await _select_forecast(found, latitude, longitude)
        if not days:
            # A grid model forecasts every point it covers, so zero days after a
            # successful discover is the provider failing, not an absence of
            # forecast. Mandi does not do this -- a market really can have no
            # arrivals. The difference is the provider, not the protocol.
            logger.warning(
                f"Weather v2 select returned no forecast days for {latitude},{longitude}; "
                "treating as provider failure, not as absence of forecast"
            )
            return _UNAVAILABLE + await agromet_note(ctx, latitude, longitude)

        return _render(days) + await agromet_note(ctx, latitude, longitude)

    except httpx.TimeoutException:
        logger.error("Weather v2 request timed out")
        return "Weather request timed out. Please try again."
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Weather v2 request failed: {e.response.status_code} {e.response.text[:500]}"
        )
        return _UNAVAILABLE
    except httpx.RequestError as e:
        logger.error(f"Weather v2 request failed: {e}")
        return f"Weather request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("Weather v2 request exceeded retry limit")
        return "Weather data is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error getting weather forecast (v2): {e}")
        raise ModelRetry(f"Unexpected error in weather forecast. {str(e)}")
