"""
Weather forecast over Beckn v2.

Same tool, same arguments as agents.tools.weather -- only the transport
changes. v1 posted one /search to the BAP and let it decide; v2 splits that
into two calls against the consumer adapter:

    discover  -> is there a forecast provider covering this point  (catalog)
    select    -> the forecast itself for this point                (live fetch)

Both versions end at the same upstream. v1 asks for it by name --
weather.py sends "Weather-Forecast-Mausamgram" -- and v2 reaches the same
IMD Mausamgram NWP through the registered `mausamgram` provider. Only the
route differs.

WHY THIS DOES NOT REUSE v1's RENDERER, unlike mandi_v2, which imports
format_output from mandi so the two can be diffed character for character:

v1's output shape is whatever the old BAP chose to send. Its renderer is
generic -- Item.__str__ prints each tag as "name: value" and knows nothing
about weather -- so it has no fixed field list to match. More decisively, the
provider-side select mapping
(helmcharts/quick-start/config/mappings/mausamgram/weather-observation.select.yaml)
deliberately drops upstream fields the v0.1 schema pack cannot hold: cloud
cover, raw (non-bias-corrected) temperature, the prose rain message, and the
compass form of wind direction. If v1 printed any of those, no amount of
re-shaping on this side brings them back.

So the two versions cannot be byte-compared, and pretending otherwise by
rebuilding the v1 envelope would be work spent serving a comparison that
cannot be exact. This module renders the v2 shape directly instead.

!! NOT YET COMPARED AGAINST v1 !!
The v1 baseline was never recorded -- it needs the old BAP on port 8095, which
nobody has started. Until that happens we do not know whether v1's answer
carried cloud cover or any other dropped field, so we cannot say this output
is complete, only that it is correct about what it does report. See
migration-plan/weather/plan.md, Phase 2b, and the TODOs below.

!! NO FORECAST HAS EVER RENDERED FROM LIVE DATA !!
IMD Mausamgram is, as of 2026-11-19, returning no forecast data to anyone:
roughly three requests in four fail outright, and the ones that succeed carry
fcstday1..5 as empty objects with lat_r/lon_r null. Everything up to the
provider is exercised and working -- discover, signing, schema validation,
routing, the mapping, and the unavailable-vs-no-data split. The renderer below
is checked against the shape the mapping documents, not against bytes that
have come back over the wire. Re-run migration-plan/weather/quick-try.md when
IMD is serving again.
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

# Offers are a provider-side concept the adapter does not cross-check against
# the catalog, and discover does not return one. This is the id the worked
# select example carries, kept so the request matches it.
_OFFER_ID = "offer:weather-observation:mausamgram:open-data"

# Word-for-word from v1 (weather.py, WeatherResponse.__str__). The skill prompt
# and the model's behaviour on an empty answer are written against this exact
# sentence, so it is copied rather than reworded.
_NO_DATA = "No weather forecast data found for the requested location."

# What the mapping emits per parameter, in the order a farmer reads them:
# rain first, then temperature, then the rest. Anything the provider sends that
# is not listed here still renders -- see _render_parameter -- it just sorts
# last, so a new parameter appearing upstream is never silently dropped.
_PARAMETER_ORDER = [
    "Rainfall",
    "Temperature",
    "Humidity",
    "WindSpeed",
    "WindDirection",
    "Alert",
]

# How the grouped `values` object reads back as a sentence. The mapping groups
# every statistic for one parameter under `values`, closed over exactly these
# five keys, so this table is complete by construction.
_STATISTIC_LABEL = {
    "instantaneous": "",
    "minimum": "min",
    "maximum": "max",
    "mean": "avg",
    "sum": "",
}


def _adapter_url(action: str) -> str:
    base = os.getenv("OAN_ADAPTER_URL", "http://network-adapter:9201/consumer")
    return base.rstrip("/") + "/" + action


def _context(action: str) -> dict:
    """The v2 envelope. One network id on all three fields: this pilot is a
    single-network deployment, so sender, receiver and network are the same."""
    network_id = os.getenv("OAN_NETWORK_ID", "oan.dev")
    return {
        "domain": os.getenv("OAN_DOMAIN", "agriculture"),
        "action": action,
        "version": "2.0.0",
        "networkId": network_id,
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

    Unlike mandi, there is nothing to rank. Mausamgram publishes ONE resource
    for its whole 0.25 degree grid domain and snaps any coordinate inside it to
    the nearest cell, so discover answers with that single resource or with
    nothing. If a second forecast provider ever registers, this takes the first
    and that becomes a real choice worth making deliberately -- which is the
    network's job, not this tool's.

    NOTE the coverage area is a bounding box (68-97.5E, 6.5-37.5N), not India's
    outline. A point in the Bay of Bengal is inside it and will discover
    successfully; the forecast that comes back is a real sea-point forecast.
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
    found = _first_resource(payload)
    if found:
        return found

    # Retry without networkId, because discovery matches a catalog's visible_to
    # against the caller's networkId as an exact string and gives "*" no special
    # meaning. The consequence, measured on this stack:
    #
    #     catalog visible_to   caller networkId=oan.dev   caller omits it
    #     {oan.dev}  (mandi)   found                      not found
    #     {*}        (weather) NOT found                  found
    #
    # So a catalog published public -- which is what the weather one is, IMD
    # forecast data being open -- is invisible to every caller that names its
    # own network, which is the opposite of what "*" should mean. There is no
    # single networkId that finds both capabilities.
    #
    # TODO(weather-v2): this belongs in the discovery service, which should read
    # "*" as "every network". Until it does, asking twice is what lets one tool
    # work against both a scoped and a public catalog without knowing in advance
    # which it is dealing with. Delete this retry once "*" matches.
    body["context"] = {k: v for k, v in _context("discover").items() if k != "networkId"}
    logger.info("Weather v2 discover: retrying without networkId (public catalog)")
    return _first_resource(await _post("discover", body))


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

    The request echoes the catalog's own resourceAttributes back with a Point
    location added. The mapping's precondition requires that location and
    refuses anything else, so it is built here explicitly rather than copied
    from whatever discover happened to return.

    No date range is sent, and that is deliberate rather than an omission: the
    upstream takes lat and lon only and always answers its full horizon. A
    validity window here would be a promise the provider does not honour.
    """
    resource = found["resource"]
    attrs = dict(resource.get("resourceAttributes") or {})

    request_attrs = {
        "@context": _SCHEMA_CONTEXT,
        "@type": _SCHEMA_TYPE,
        "informationMode": "OnDemand",
        "subjectCategories": attrs.get("subjectCategories") or ["Weather"],
        "supportedObservationTypes": attrs.get("supportedObservationTypes") or ["Forecast"],
        "supportedParameters": attrs.get("supportedParameters") or list(_PARAMETER_ORDER),
        "geographicGranularities": attrs.get("geographicGranularities") or ["Point"],
        # GeoJSON is [longitude, latitude]. Swapped, this is still a valid
        # request -- for a point in the wrong place -- so it would fail as
        # quietly wrong data rather than as an error.
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
            # Only Direct resources carry readings. An OnDemand one coming back
            # means the provider echoed the catalog entry instead of answering.
            if res_attrs.get("informationMode") == "Direct" and res_attrs.get("parameters"):
                days.append(res)
    return days


def _day_label(resource: dict) -> str:
    """The forecast day, as DD-MM-YYYY.

    Read off validity.startsAt, which the mapping opens at 00:00:00+05:30 on
    the day the resource describes. Sliced before parsing rather than parsed
    with an offset-aware reader, because only the date is wanted and the offset
    is fixed IST.
    """
    attrs = resource.get("resourceAttributes") or {}
    starts_at = ((attrs.get("validity") or {}).get("startsAt") or "")[:10]
    if not starts_at:
        return "Unknown date"
    try:
        return datetime.strptime(starts_at, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return starts_at


def _render_parameter(entry: dict) -> Optional[str]:
    """One parameter's readings as a line: "Temperature: min 21.4, max 33.8 Cel".

    Alert carries IMD's own daily condition text, which is present every day and
    most days says something routine like "Overcast Sky". It is rendered as
    plain text with no unit, because "Overcast Sky 1" is not a sentence.
    """
    name = entry.get("parameter") or ""
    values = entry.get("values") or {}
    unit = entry.get("unit") or ""
    if not name or not values:
        return None

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
    """Known parameters in reading order; anything new sorts last but still
    renders, so an upstream addition shows up rather than disappearing."""
    name = entry.get("parameter") or ""
    if name in _PARAMETER_ORDER:
        return (_PARAMETER_ORDER.index(name), "")
    return (len(_PARAMETER_ORDER), name)


def _render(days: List[dict]) -> str:
    """The v2 answer as text.

    Flatter than v1, which nested Catalog > Providers > Provider > Items > Item
    > Tags around the same numbers. The heading and the no-data sentence are
    v1's exactly; what sits between them is this shape, not v1's.
    """
    lines = [f"**Weather Forecast Data** [Today's Date: {get_today_date_str()}]"]
    if not days:
        lines.append(_NO_DATA)
        return "\n".join(lines)

    first = (days[0].get("resourceAttributes") or {})
    source = (first.get("source") or {}).get("sourceName") or "IMD Mausamgram NWP"
    coords = ((first.get("location") or {}).get("geo") or {}).get("coordinates") or []
    if len(coords) >= 2:
        # The grid cell the request snapped to, not the point asked for -- up to
        # ~14 km away. Stated so a farmer comparing two nearby villages can see
        # why they got the same numbers.
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
            # agromet_note is appended even with no forecast, exactly as v1 did:
            # it answers a different question (is there a live IMD advisory for
            # this place) and its answer does not depend on this one.
            return _render([]) + await agromet_note(ctx, latitude, longitude)

        days = await _select_forecast(found, latitude, longitude)
        if not days:
            # TODO(weather-v2): currently indistinguishable from a provider
            # outage, which is the same gap mandi still carries. A grid model
            # answers every point in its domain, so an empty answer here is far
            # more likely to be a failure than a real "no forecast" -- but
            # nothing in the response says which. Confirm against the adapter's
            # error shape and split the two.
            logger.warning(
                f"Weather v2 select returned no forecast days for {latitude},{longitude}"
            )
        return _render(days) + await agromet_note(ctx, latitude, longitude)

    except httpx.TimeoutException:
        logger.error("Weather v2 request timed out")
        return "Weather request timed out. Please try again."
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Weather v2 request failed: {e.response.status_code} {e.response.text[:500]}"
        )
        return "Weather service unavailable. Please try again later."
    except httpx.RequestError as e:
        logger.error(f"Weather v2 request failed: {e}")
        return f"Weather request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("Weather v2 request exceeded retry limit")
        return "Weather data is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error getting weather forecast (v2): {e}")
        raise ModelRetry(f"Unexpected error in weather forecast. {str(e)}")
