"""Tool-level v1-vs-v2 check for the mandi migration pilot.

Calls get_mandi_prices with fixed arguments and prints exactly what the agent
would have received. No LLM and no geocoder: the coordinates and the English
commodity name are passed straight in, which is what forward_geocode and
search_commodity would have produced. That keeps the comparison deterministic --
any difference between the two runs is the transport, not the model.

    MANDI_BECKN_VERSION=v1 python scripts/pilot_mandi_check.py > v1.txt
    MANDI_BECKN_VERSION=v2 python scripts/pilot_mandi_check.py > v2.txt
    diff v1.txt v2.txt

With no arguments it runs the fixed case list below. Pass arguments to ask one
question of your own instead:

    python scripts/pilot_mandi_check.py --lat 19.6445 --lon 74.6619 \
        --place Rahuri --commodity Onion --from 01-09-2026 --to 09-09-2026

Add --show-payloads to print the discover and select bodies that go on the wire
alongside the answer.

Coordinates are the published markets themselves, because the pilot catalog
holds only Rahuri and Pune; a farmer's town 99 km away has no coverage in this
network and would make every case read NO_DATA for the wrong reason.
"""
import argparse
import asyncio
import json
import os
from dataclasses import dataclass

VERSION = os.getenv("MANDI_BECKN_VERSION", "v1").strip().lower()
if VERSION == "v2":
    from agents.tools.v2.mandi import get_mandi_prices
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


def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lat", type=float, help="latitude, as forward_geocode would return")
    ap.add_argument("--lon", type=float, help="longitude")
    ap.add_argument("--place", default="", help="place name the farmer used, e.g. Rahuri")
    ap.add_argument("--commodity", help="English commodity name, e.g. Onion")
    ap.add_argument("--from", dest="date_from", help="DD-MM-YYYY; omit for latest available")
    ap.add_argument("--to", dest="date_to", help="DD-MM-YYYY; only for a range")
    ap.add_argument("--nearby", action="store_true", help="accept prices from the nearest mandi with data")
    ap.add_argument("--show-payloads", action="store_true", help="print the bodies sent to the adapter")
    return ap.parse_args()


def _trace_payloads():
    """Print every request the v2 tool puts on the wire. v1 is left alone --
    it builds one payload inline and there is nothing to hook."""
    if VERSION != "v2":
        print("(--show-payloads only applies to v2)")
        return
    import agents.tools.v2.mandi as v2

    original = v2._post

    async def traced(action, body):
        print(f"\n----- {action} request -----")
        print(json.dumps(body, indent=2))
        return await original(action, body)

    v2._post = traced


async def main():
    args = _parse_args()
    if args.show_payloads:
        _trace_payloads()

    if args.commodity and (args.place or (args.lat is not None and args.lon is not None)):
        latitude, longitude = args.lat, args.lon
        if latitude is None or longitude is None:
            # Geocode the place the same way the chat path does, so a question
            # asked here and the same question asked through the model land on
            # the same coordinates. Without this the place name was accepted
            # and silently ignored, and the run fell through to CASES -- which
            # looks like an answer, just not to the question that was asked.
            from agents.tools.maps import locate

            found = await locate(args.place)
            if not found:
                raise SystemExit(f"could not geocode {args.place!r}; pass --lat and --lon instead")
            location, _kind = found
            latitude, longitude = location.latitude, location.longitude
            print(f"{args.place} -> {latitude}, {longitude}")

        cases = [("your question", dict(
            latitude=latitude, longitude=longitude, location_name=args.place,
            commodity_name=args.commodity, price_date=args.date_from,
            price_date_to=args.date_to, include_nearby_mandis=args.nearby))]
    elif args.commodity or args.place or args.lat is not None:
        # Half a question is not a question. Saying so beats running the seven
        # fixed cases and letting the caller think they were answered.
        raise SystemExit("need --commodity plus either --place or both --lat and --lon")
    else:
        cases = CASES

    for label, kwargs in cases:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        try:
            print(await get_mandi_prices(_Ctx(), **kwargs))
        except Exception as e:
            print(f"!! {type(e).__name__}: {e}")


asyncio.run(main())
