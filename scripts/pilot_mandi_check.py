"""Tool-level v1-vs-v2 check for the mandi migration pilot.

Calls get_mandi_prices with fixed arguments and prints exactly what the agent
would have received. No LLM and no geocoder: the coordinates and the English
commodity name are passed straight in, which is what forward_geocode and
search_commodity would have produced. That keeps the comparison deterministic --
any difference between the two runs is the transport, not the model.

    MANDI_BECKN_VERSION=v1 python scripts/pilot_mandi_check.py > v1.txt
    MANDI_BECKN_VERSION=v2 python scripts/pilot_mandi_check.py > v2.txt
    diff v1.txt v2.txt

Coordinates are the published markets themselves, because the pilot catalog
holds only Rahuri and Pune; a farmer's town 99 km away has no coverage in this
network and would make every case read NO_DATA for the wrong reason.
"""
import asyncio
import os
from dataclasses import dataclass

VERSION = os.getenv("MANDI_BECKN_VERSION", "v1").strip().lower()
if VERSION == "v2":
    from agents.tools.mandi_v2 import get_mandi_prices
else:
    from agents.tools.mandi import get_mandi_prices


@dataclass
class _Deps:
    session_id: str = "pilot-check"
    question_id: str = "pilot-check"


class _Ctx:
    """Stands in for pydantic-ai's RunContext; the tool only reads .deps."""

    deps = _Deps()


RAHURI = dict(latitude=19.6445, longitude=74.6619, location_name="Rahuri")
PUNE = dict(latitude=18.4909, longitude=73.8685, location_name="Pune")
NASHIK = dict(latitude=19.9975, longitude=73.7898, location_name="Nashik")

CASES = [
    ("Rahuri / Onion / 01-09 to 09-09-2026 (range)",
     dict(**RAHURI, commodity_name="Onion", price_date="01-09-2026", price_date_to="09-09-2026")),
    ("Rahuri / Onion / 01-09-2026 (single day with arrivals)",
     dict(**RAHURI, commodity_name="Onion", price_date="01-09-2026")),
    ("Rahuri / Onion / 02-09-2026 (single day, no arrivals)",
     dict(**RAHURI, commodity_name="Onion", price_date="02-09-2026")),
    ("Rahuri / Onion / latest available (no date)",
     dict(**RAHURI, commodity_name="Onion")),
    ("Pune / Onion / 01-09 to 09-09-2026 (second market)",
     dict(**PUNE, commodity_name="Onion", price_date="01-09-2026", price_date_to="09-09-2026")),
    ("Nashik / Onion / 01-09 to 09-09-2026 (no market in range)",
     dict(**NASHIK, commodity_name="Onion", price_date="01-09-2026", price_date_to="09-09-2026")),
    ("Rahuri / Onion / 01-01 to 09-09-2026 (over the 30-day limit)",
     dict(**RAHURI, commodity_name="Onion", price_date="01-01-2026", price_date_to="09-09-2026")),
]


async def main():
    for label, kwargs in CASES:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        try:
            print(await get_mandi_prices(_Ctx(), **kwargs))
        except Exception as e:
            print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())
