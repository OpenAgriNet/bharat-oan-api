"""
Mandi price discovery over Beckn v2.

Same tool, same arguments, same output as agents.tools.mandi -- only the
transport changes. v1 posted one /search to the BAP and let the provider decide
which mandi answered; v2 splits that into two calls against the consumer
adapter:

    discover  -> which markets near here trade this commodity  (catalog)
    select    -> what are this market's prices for these dates (live fetch)

Everything downstream of the HTTP call is reused verbatim from the v1 module --
the date helpers, MandiItem, and format_output -- so the two versions can be
diffed output-against-output rather than eyeballed. See documents/
migration-pilot-plan.md, Phase 3.
"""
import math
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
from agents.tools.commodity import COMMODITY_ENTRIES
from agents.tools.mandi import (
    Descriptor,
    MandiItem,
    Tag,
    TagItem,
    _build_merged_response,
    _capped_range,
    _range_too_long,
    _resolve_date_range,
)
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)

_SCHEMA_CONTEXT = (
    "https://openagrinet.github.io/network-specs/schema/MandiPrice/v0.1/context.jsonld"
)
_SCHEMA_TYPE = "openagrinet:MandiPrice"

# 50 km, matching the radius v1's provider used for its nearest-mandi fallback;
# format_output's NO_DATA wording ("within 50 km") is written against it.
_SEARCH_RADIUS_METERS = 50_000

# Nothing in the published catalog declares an offer -- offers are a provider-side
# concept the adapter does not cross-check against the catalog. This is the id the
# working API-collection call used, kept so the request stays byte-identical to it.
_OFFER_ID = "offer:mandi-price:open-data"

# How many markets to try before giving up. The nearest is the farmer's mandi;
# the next one is the "nearest mandi with data" of NO_DATA_AT_REQUESTED_MANDI.
_MAX_MARKETS_TRIED = 2

_IST_OFFSET = "+05:30"


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


def _commodity_code(commodity_name: str) -> Optional[str]:
    """AGMKT code for the English name search_commodity returned.

    Exact name match first -- that is the column the skill prompt tells the model
    to pass. The term list is the fallback for a model that passed a synonym.
    """
    wanted = (commodity_name or "").strip().casefold()
    if not wanted:
        return None
    for entry in COMMODITY_ENTRIES:
        if entry.name.casefold() == wanted:
            return str(entry.code)
    for entry in COMMODITY_ENTRIES:
        if any(term.casefold() == wanted for term in entry.terms):
            return str(entry.code)
    return None


def _to_iso_window(from_date: str, to_date: str) -> Tuple[str, str]:
    """DD-MM-YYYY pair -> the IST day-boundary window the catalog's validity uses."""
    start, end = _capped_range(from_date, to_date)
    return (
        f"{start.isoformat()}T00:00:00{_IST_OFFSET}",
        f"{end.isoformat()}T23:59:59{_IST_OFFSET}",
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _resource_point(resource: dict) -> Optional[Tuple[float, float]]:
    """(lat, lon) of a market. GeoJSON orders coordinates lon-first."""
    attrs = resource.get("resourceAttributes") or {}
    geo = ((attrs.get("market") or {}).get("location") or {}).get("geo") or {}
    coords = geo.get("coordinates")
    if not coords:
        for area in attrs.get("coverageAreas") or []:
            if area.get("type") == "Point" and area.get("coordinates"):
                coords = area["coordinates"]
                break
    if not coords or len(coords) < 2:
        return None
    return float(coords[1]), float(coords[0])


async def _post(action: str, body: dict) -> dict:
    url = _adapter_url(action)
    logger.info(f"Mandi v2 {action} -> {url}")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json()


async def _discover_markets(
    latitude: float, longitude: float, commodity_code: str
) -> List[dict]:
    """Markets within the radius that trade this commodity, nearest first.

    The jsonpath filter and the spatial clause are both evaluated by the discovery
    service, so this returns only candidates; the distance sort is ours, because
    the response carries no ranking.
    """
    body = {
        "context": _context("discover"),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        "$.catalogs[*].resources[*] ? ("
                        '@.resourceAttributes.subjectCategories[*] == "Market" && '
                        '@.resourceAttributes.informationMode == "OnDemand" && '
                        f'@.resourceAttributes.supportedCommodities[*].code == "{commodity_code}")'
                    ),
                },
                "spatial": [
                    {
                        "op": "S_DWITHIN",
                        "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
                        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                        "distanceMeters": _SEARCH_RADIUS_METERS,
                        "quantifier": "ANY",
                    }
                ],
            }
        },
    }
    payload = await _post("discover", body)

    found: List[Tuple[float, dict, dict]] = []
    for catalog in payload.get("message", {}).get("catalogs", []) or []:
        provider = catalog.get("provider") or {}
        for resource in catalog.get("resources", []) or []:
            point = _resource_point(resource)
            distance = (
                _haversine_km(latitude, longitude, point[0], point[1])
                if point
                else float("inf")
            )
            found.append((distance, resource, provider))

    found.sort(key=lambda row: row[0])
    return [
        {"distance_km": distance, "resource": resource, "provider": provider}
        for distance, resource, provider in found
    ]


def _is_empty_result_nack(response: httpx.Response) -> bool:
    """True when the adapter's failure is really "this market has nothing".

    Agmarknet answers a date with no arrivals with HTTP 400 and
    {"success": false, "message": "No data available for the selected date
    range."}. The provider adapter has no rule that reads that as an empty
    result, so it reports the upstream as unreachable and the call comes back
    502 NET_DOWNSTREAM_UNAVAILABLE. v1 gave the farmer NO_DATA for the same
    question, so swallowing it here keeps the two versions comparable.

    This is a workaround and it is lossy: a genuine provider outage now reads
    as NO_DATA too. Every occurrence is logged at warning with the adapter's
    own text, so the two cases are at least distinguishable after the fact.

    The mapping cannot fix this, though an earlier version of this comment said
    it could. The adapter abandons the call before any mapping runs: anything
    outside 2xx returns an error from the HTTP step, and the upstream body --
    the part that says "No data available" rather than "we are down" -- is
    logged and dropped on purpose, because a failure body can carry a stack
    trace or an internal hostname (beckn-onix http.go:143). ActionPlan has no
    field for an acceptable non-2xx status either (model.go:114), so there is
    nowhere to say "400 here means empty". Lifting this needs an adapter change
    and a new image, not a mapping edit.
    """
    if response.status_code != 502:
        return False
    try:
        error = (response.json().get("message") or {}).get("error") or {}
    except ValueError:
        return False
    return error.get("code") == "NET_DOWNSTREAM_UNAVAILABLE"


async def _select_prices(
    market: dict, commodity_code: str, commodity_name: str, from_date: str, to_date: str
) -> List[dict]:
    """Invoke one market for its prices over the window; returns Direct resources.

    The request echoes the catalog's own resourceAttributes back, narrowed to the
    one commodity asked for and with validity replaced by the requested window --
    so the provider adapter reads the market and date range from the same fields
    it published.
    """
    resource = market["resource"]
    attrs = dict(resource.get("resourceAttributes") or {})
    starts_at, ends_at = _to_iso_window(from_date, to_date)

    request_attrs = {
        "@context": _SCHEMA_CONTEXT,
        "@type": _SCHEMA_TYPE,
        "informationMode": "OnDemand",
        "subjectCategories": attrs.get("subjectCategories") or ["Market"],
        "supportedCommodities": [{"code": commodity_code, "name": commodity_name}],
        "supportedPriceFields": attrs.get("supportedPriceFields")
        or ["Minimum", "Maximum", "Modal"],
        "market": attrs.get("market") or {},
        "validity": {"startsAt": starts_at, "endsAt": ends_at},
    }

    body = {
        "context": {**_context("select"), "bppUri": f"https://{os.getenv('OAN_NETWORK_ID', 'oan.dev')}"},
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
                            "provider": market["provider"],
                        },
                    }
                ],
            }
        },
    }
    try:
        payload = await _post("select", body)
    except httpx.HTTPStatusError as e:
        if _is_empty_result_nack(e.response):
            logger.warning(
                f"Treating select failure as no data for {resource.get('id')} "
                f"{from_date}..{to_date}: {e.response.text[:300]}"
            )
            return []
        raise

    priced: List[dict] = []
    for commitment in payload.get("message", {}).get("contract", {}).get("commitments", []) or []:
        for res in commitment.get("resources", []) or []:
            attrs = res.get("resourceAttributes") or {}
            # Only Direct resources carry prices. An OnDemand one coming back here
            # means the provider echoed the catalog entry instead of answering.
            if attrs.get("informationMode") == "Direct" and attrs.get("prices"):
                priced.append(res)
    return priced


def _tag(code: str, value: Any) -> TagItem:
    return TagItem(descriptor=Descriptor(code=code, name=code), value=str(value))


def _iso_to_ddmmyyyy(value: Optional[str]) -> str:
    """ISO arrival date -> the DD-MM-YYYY the v1 items carried, so the date
    helpers and the rendered output are identical across versions."""
    if not value:
        return ""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return value


def _to_mandi_item(resource: dict) -> Optional[MandiItem]:
    """One priced v2 resource -> the v1 MandiItem format_output already renders."""
    attrs = resource.get("resourceAttributes") or {}
    prices = attrs.get("prices") or {}
    market = attrs.get("market") or {}
    commodities = attrs.get("supportedCommodities") or []
    commodity = (commodities[0].get("name") if commodities else "") or ""

    pairs = [
        ("Commodity", commodity),
        ("Market", (market.get("marketName") or "").strip()),
        ("District", market.get("district") or ""),
        ("State", market.get("state") or ""),
        ("Modal Price", prices.get("modal")),
        ("Min Price", prices.get("minimum")),
        ("Max Price", prices.get("maximum")),
        ("Price Unit", prices.get("unit") or ""),
        ("Arrival Date", _iso_to_ddmmyyyy(attrs.get("arrivalDate"))),
        ("Variety", attrs.get("variety") or ""),
        ("Grade", attrs.get("grade") or ""),
    ]
    items = [_tag(code, value) for code, value in pairs if value not in (None, "")]
    if not items:
        return None

    return MandiItem(
        id=resource.get("id") or str(uuid.uuid4()),
        descriptor=Descriptor(name=commodity or None),
        matched=True,
        tags=[Tag(descriptor=Descriptor(code="mandi-price", name="Mandi Price"), list=items)],
    )


@observe(name="tool:get_mandi_prices", as_type="tool")
async def get_mandi_prices(
    ctx: RunContext[FarmerContext],
    latitude: float,
    longitude: float,
    location_name: str,
    commodity_name: str,
    price_date: Optional[str] = None,
    price_date_to: Optional[str] = None,
    include_nearby_mandis: bool = False,
) -> str:
    """Get mandi prices for a specific commodity near a location.

    Use this tool to fetch commodity price information from nearby mandis (agricultural markets).
    Use forward_geocode for coordinates and location_name (city or district from the query).
    Use search_commodity to resolve the English commodity name (the name column, e.g. Onion).

    Args:
        latitude (float): Latitude of the location
        longitude (float): Longitude of the location
        location_name (str): City or district name (e.g. Jaipur, Pune); when the farmer names a
            specific mandi, pass the mandi name (e.g. Azadpur) so the result says whether that
            mandi itself has data
        commodity_name (str): English commodity name from search_commodity (e.g. Onion)
        price_date (str): Optional price date in DD-MM-YYYY; pass for today/yesterday/specific dates.
            For a date range ("from 01-07-2026 to 10-07-2026") pass the start date here.
        price_date_to (str): Optional end date in DD-MM-YYYY; pass only for a date range, together
            with price_date as the start date (e.g. price_date=01-07-2026, price_date_to=10-07-2026).
            A range may span at most 30 days.
        include_nearby_mandis (bool): Pass true only after the farmer has chosen to see prices
            from the nearest mandi with data, following a NO_DATA_AT_REQUESTED_MANDI result.

    Returns:
        str: Formatted mandi price data for the requested commodity and location
    """
    too_long = _range_too_long(price_date, price_date_to)
    if too_long:
        return too_long

    try:
        from_date, to_date = _resolve_date_range(price_date, price_date_to)
        commodity_code = _commodity_code(commodity_name)

        lf_update_current_observation(
            input={
                "latitude": latitude,
                "longitude": longitude,
                "location_name": location_name,
                "commodity_name": commodity_name,
                "price_date": price_date,
                "price_date_to": price_date_to,
                "include_nearby_mandis": include_nearby_mandis,
                "from_date": from_date,
                "to_date": to_date,
            },
            metadata={"tool": "mandi.prices", "beckn_version": "v2"},
        )

        if not commodity_code:
            logger.warning(f"No AGMKT code for commodity {commodity_name!r}")
            return _empty_output(
                commodity_name, location_name, price_date, price_date_to, include_nearby_mandis
            )

        markets = await _discover_markets(latitude, longitude, commodity_code)
        if not markets:
            return _empty_output(
                commodity_name, location_name, price_date, price_date_to, include_nearby_mandis
            )

        # The nearest market is the farmer's. Anything after it is "a nearby mandi",
        # which is what makes the difference between NO_DATA and
        # NO_DATA_AT_REQUESTED_MANDI -- v1 got that split from the provider;
        # here it comes from the order of the discover results.
        search_context: Dict[str, str] = {
            "commodity": commodity_name,
            "requested_location": location_name,
            "status": "no_data",
        }
        priced: List[dict] = []
        for index, market in enumerate(markets[:_MAX_MARKETS_TRIED]):
            resources = await _select_prices(
                market, commodity_code, commodity_name, from_date, to_date
            )
            if not resources:
                continue
            priced = resources
            if index == 0:
                search_context["status"] = "data_found"
            else:
                attrs = market["resource"].get("resourceAttributes") or {}
                market_info = attrs.get("market") or {}
                search_context.update(
                    status="nearby_only",
                    market=(market_info.get("marketName") or "").strip(),
                    district=str(market_info.get("district") or ""),
                    distance_km=f"{market['distance_km']:.0f}",
                )
            break

        items = [item for item in (_to_mandi_item(r) for r in priced) if item]
        response = _build_merged_response({"context": _context("select")}, items)
        return response.format_output(
            requested_price_date=price_date,
            requested_price_date_to=price_date_to,
            search_context=search_context,
            include_nearby_mandis=include_nearby_mandis,
        )

    except httpx.TimeoutException:
        logger.error("Mandi v2 request timed out")
        return "Mandi price request timed out. Please try again."
    except httpx.HTTPStatusError as e:
        logger.error(f"Mandi v2 request failed: {e.response.status_code} {e.response.text[:500]}")
        return f"Mandi price request failed: {e.response.status_code}"
    except httpx.RequestError as e:
        logger.error(f"Mandi v2 request failed: {e}")
        return f"Mandi price request failed: {str(e)}"
    except UnexpectedModelBehavior:
        logger.warning("Mandi v2 request exceeded retry limit")
        return "Mandi price data is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error(f"Error getting mandi prices (v2): {e}")
        raise ModelRetry(f"Unexpected error in mandi price request. {str(e)}")


def _empty_output(
    commodity_name: str,
    location_name: str,
    price_date: Optional[str],
    price_date_to: Optional[str],
    include_nearby_mandis: bool,
) -> str:
    """The NO_DATA text, produced by format_output rather than written out here,
    so the wording cannot drift from v1's."""
    response = _build_merged_response({"context": _context("select")}, [])
    return response.format_output(
        requested_price_date=price_date,
        requested_price_date_to=price_date_to,
        search_context={
            "commodity": commodity_name,
            "requested_location": location_name,
            "status": "no_data",
        },
        include_nearby_mandis=include_nearby_mandis,
    )
