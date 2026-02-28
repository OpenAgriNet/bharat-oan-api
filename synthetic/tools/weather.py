"""
Mock weather tool — generates randomized weather forecasts.
Keeps the same Pydantic models and function signature as agents/tools/weather.py
but replaces the HTTP call with random data generation.
"""

import random
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from typing import List, Optional
from pydantic_ai.tools import RunContext
from synthetic.deps import FarmerContext
from synthetic.mock_data import (
    WEATHER_CONDITIONS,
    WIND_DIRECTIONS,
    random_humidity,
    random_rainfall,
    random_wind_speed,
    should_fail,
)


# ─── Simplified Pydantic Models (matching real response __str__ format) ────────

class Descriptor(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None

    def __str__(self) -> str:
        return self.name or self.code or ""


class TagItem(BaseModel):
    descriptor: Descriptor
    value: str

    def __str__(self) -> str:
        desc_name = self.descriptor.name or self.descriptor.code or "Tag"
        return f"{desc_name}: {self.value}"


class Tag(BaseModel):
    descriptor: Descriptor
    list: List[TagItem]

    def __str__(self) -> str:
        group_name = self.descriptor.name or self.descriptor.code or "Details"
        items_str = "\n      ".join(str(tag_item) for tag_item in self.list)
        return f"{group_name}:\n      {items_str}"


class Item(BaseModel):
    id: str
    descriptor: Descriptor
    matched: bool = True
    recommended: bool = False
    tags: Optional[List[Tag]] = None

    def __str__(self) -> str:
        lines = []
        lines.append(f"**Item:** {self.descriptor.name or self.id}")
        if self.tags:
            lines.append("  Tags:")
            for t in self.tags:
                tag_str = str(t).replace("\n", "\n    ")
                lines.append(f"    {tag_str}")
        return "\n".join(lines)


class Provider(BaseModel):
    id: str
    descriptor: Descriptor
    items: Optional[List[Item]] = None

    def __str__(self) -> str:
        lines = []
        lines.append(f"Provider: {self.descriptor.name or self.id}")
        if self.items:
            lines.append("  Items:")
            for item in self.items:
                item_str = str(item).replace("\n", "\n    ")
                lines.append(f"    {item_str}")
        return "\n".join(lines)


# ─── Mock Forecast Generation ──────────────────────────────────────────────────

def _generate_day_forecast(date: datetime) -> Item:
    """Generate a single day's weather forecast as an Item."""
    date_str = date.strftime("%Y-%m-%d")
    condition = random.choice(WEATHER_CONDITIONS)

    month = date.month
    if month in (4, 5, 6):
        min_temp = round(random.uniform(26, 34), 1)
        max_temp = round(random.uniform(min_temp + 4, 45), 1)
    elif month in (12, 1, 2):
        min_temp = round(random.uniform(5, 18), 1)
        max_temp = round(random.uniform(min_temp + 5, 28), 1)
    elif month in (7, 8, 9):
        min_temp = round(random.uniform(22, 28), 1)
        max_temp = round(random.uniform(min_temp + 3, 36), 1)
    else:
        min_temp = round(random.uniform(15, 25), 1)
        max_temp = round(random.uniform(min_temp + 5, 35), 1)

    tag_items = [
        TagItem(descriptor=Descriptor(code="date", name="Date"), value=date_str),
        TagItem(descriptor=Descriptor(code="condition", name="Condition"), value=condition),
        TagItem(descriptor=Descriptor(code="min_temp", name="Min Temperature (°C)"), value=str(min_temp)),
        TagItem(descriptor=Descriptor(code="max_temp", name="Max Temperature (°C)"), value=str(max_temp)),
        TagItem(descriptor=Descriptor(code="humidity", name="Humidity (%)"), value=str(random_humidity())),
        TagItem(descriptor=Descriptor(code="rainfall", name="Rainfall (mm)"), value=str(random_rainfall())),
        TagItem(descriptor=Descriptor(code="wind_speed", name="Wind Speed (km/h)"), value=str(random_wind_speed())),
        TagItem(descriptor=Descriptor(code="wind_direction", name="Wind Direction"), value=random.choice(WIND_DIRECTIONS)),
    ]

    return Item(
        id=f"weather-{date_str}",
        descriptor=Descriptor(code=date_str, name=f"Weather Forecast - {date_str}"),
        tags=[Tag(descriptor=Descriptor(code="forecast", name="Forecast Details"), list=tag_items)],
    )


async def weather_forecast(ctx: RunContext[FarmerContext], latitude: float, longitude: float) -> str:
    """Get Weather forecast for a specific location.

    Args:
        latitude (float): Latitude of the location
        longitude (float): Longitude of the location

    Returns:
        str: The weather forecast for the specific location
    """
    # 5% chance of simulated failure
    if should_fail():
        return "Weather forecast service is temporarily unavailable. Please try again later."

    today = ctx.deps.today_date
    num_days = random.randint(5, 7)
    items = [_generate_day_forecast(today + timedelta(days=i)) for i in range(num_days)]

    provider = Provider(
        id="mausamgram",
        descriptor=Descriptor(code="WFC", name="Mausamgram Weather Forecast"),
        items=items,
    )

    lines = []
    lines.append(f"**Weather Forecast Data** [Today's Date: {ctx.deps.get_today_date_str()}]")
    lines.append("Responses:")
    lines.append(f"    {str(provider)}")
    return "\n".join(lines)
