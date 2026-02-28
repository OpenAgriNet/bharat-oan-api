"""
Mock mandi price tool — generates randomized crop prices.
Keeps the same function signature and output format as agents/tools/mandi.py.
Uses real APMC market names from assets/mandi_locations.csv matched by proximity.
"""

import csv
import json
import math
import random
from datetime import timedelta
from pydantic_ai.tools import RunContext
from synthetic.deps import FarmerContext
from synthetic.mock_data import (
    CROP_PRICE_RANGES,
    DEFAULT_PRICE_RANGE,
    COMMODITY_VARIETIES,
    DEFAULT_VARIETIES,
    GRADES,
    should_fail,
)

# ─── Load commodity codes for name lookup ─────────────────────────────────────

_raw = json.load(open("assets/commodity_codes.json", "r", encoding="utf-8"))
_CODE_TO_NAME = {e["code"]: e["name"] for e in _raw}


def _commodity_name(code: int) -> str:
    return _CODE_TO_NAME.get(code, f"Commodity-{code}")


# ─── Load APMC market locations from CSV ──────────────────────────────────────

_MANDI_CSV_PATH = "assets/mandi_locations.csv"
_MANDI_ENTRIES: list[dict] = []

try:
    with open(_MANDI_CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (ValueError, KeyError):
                continue
            _MANDI_ENTRIES.append({
                "state": row["state"].strip(),
                "district": row["districtname"].strip(),
                "market_name": row["marketname"].strip(),
                "lat": lat,
                "lon": lon,
            })
except FileNotFoundError:
    pass


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _get_nearby_markets(lat: float, lon: float, max_km: float = 300.0, max_markets: int = 20) -> list[dict]:
    """Find APMC markets near the given coordinates using haversine distance."""
    scored = []
    for entry in _MANDI_ENTRIES:
        dist = _haversine_km(lat, lon, entry["lat"], entry["lon"])
        if dist <= max_km:
            scored.append((dist, entry))

    scored.sort(key=lambda x: x[0])

    results = []
    for dist_km, entry in scored[:max_markets]:
        results.append({
            "market_name": entry["market_name"],
            "district": entry["district"],
            "state": entry["state"],
            "distance_km": round(dist_km),
        })

    return results


# ─── Main tool function ──────────────────────────────────────────────────────

async def get_mandi_prices(
    ctx: RunContext[FarmerContext],
    latitude: float,
    longitude: float,
    commodity_code: int,
    days_back: int = 30,
) -> str:
    """Get mandi prices for a specific commodity near a location.

    Use this tool to fetch commodity price information from nearby mandis (agricultural markets).
    You need the commodity code (use search_commodity tool to find it) and the farmer's location.

    Args:
        latitude (float): Latitude of the location
        longitude (float): Longitude of the location
        commodity_code (int): AGMKT commodity code (use search_commodity tool to find the code)
        days_back (int): Number of days to look back from today for price data (default 30)

    Returns:
        str: Formatted mandi price data for the requested commodity and location
    """
    # 5% chance of simulated service failure
    if should_fail():
        return "Mandi price service is temporarily unavailable. Please try again later."

    commodity_name = _commodity_name(commodity_code)

    # ~15% chance commodity not available at nearby mandis
    if random.random() < 0.15:
        return f"No recent price data found for {commodity_name} near the requested location. The commodity may not be traded at nearby mandis currently."

    price_range = CROP_PRICE_RANGES.get(commodity_name, DEFAULT_PRICE_RANGE)

    # Weighted item count — skews toward 3-5 results
    num_items = random.choices([1, 2, 3, 4, 5, 6, 7, 8], weights=[5, 10, 20, 25, 20, 10, 5, 5], k=1)[0]
    today = ctx.deps.today_date

    # Get nearby markets based on query location
    nearby = _get_nearby_markets(latitude, longitude)
    if not nearby:
        return f"No mandi data available near the requested location."

    # Pick num_items markets (with replacement if fewer available)
    selected = random.choices(nearby, k=num_items)

    lines = []
    lines.append(f"**Mandi Price Discovery** [Today's Date: {ctx.deps.get_today_date_str()}]")

    items = []
    for market in selected:
        modal_price = random.randint(price_range[0], price_range[1])
        min_price = modal_price - random.randint(50, 300)
        max_price = modal_price + random.randint(50, 300)

        days_ago = random.randint(0, min(days_back, 30))
        arrival_date = (today - timedelta(days=days_ago)).strftime("%d/%m/%Y")

        variety = random.choice(COMMODITY_VARIETIES.get(commodity_name, DEFAULT_VARIETIES))
        grade = random.choice(GRADES)

        item_lines = []
        item_lines.append(f"Commodity: {commodity_name}")
        item_lines.append(f"Market: {market['market_name']}, {market['district']}, {market['state']}")
        item_lines.append(f"Price: INR/Quintal {modal_price} (Min: {min_price}, Max: {max_price})")
        if days_ago == 0:
            item_lines.append(f"Arrival: {arrival_date} (today)")
        elif days_ago == 1:
            item_lines.append(f"Arrival: {arrival_date} (1 day ago)")
        else:
            item_lines.append(f"Arrival: {arrival_date} ({days_ago} days ago)")
        item_lines.append(f"Variety: {variety} | Grade: {grade}")

        items.append("\n".join(item_lines))

    lines.append("\n---\n".join(items))
    return "\n".join(lines)
