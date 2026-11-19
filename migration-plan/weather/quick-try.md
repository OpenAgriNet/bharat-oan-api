# Trying the weather use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what
changed and why, see [plan.md](plan.md).

> **Read this first.** IMD Mausamgram is not serving forecast data at the
> moment. Every request either fails outright or returns a 200 with five empty
> forecast days. So the honest expectation for every command below is
> *"Weather service unavailable"*, not a forecast. That is the system working
> correctly against a provider that is down — see
> [plan.md](plan.md#what-the-live-provider-actually-does).

Weather-specific setup, on top of the shared steps:

- `make register-provider PROVIDER=mausamgram` — needs `MAUSAMGRAM_AUTH`, one
  opaque token IMD issues
- Publish `helmcharts/quick-start/capability-examples/WeatherObservation/publish/ondemand-mausamgram.json`
- `docker-compose.pilot.yml` carries `WEATHER_BECKN_VERSION: v2`. Remove it and
  the same questions run on v1 — though v1 needs the old BAP on port 8095.

---

## Two ways to ask, and the difference matters

### The tool on its own

No model involved, so a failure here is a network or adapter failure rather
than a prompt problem. This is the one to run first.

```sh
docker exec -i -e PYTHONPATH=/app sva_app python - <<'PY'
import asyncio, logging
logging.basicConfig(level=logging.INFO)
from agents.tools.weather_v2 import _discover_provider, _select_forecast

async def main():
    found = await _discover_provider(18.52, 73.86)      # Pune
    print("discover:", found and found["resource"]["id"])
    print("select  :", len(await _select_forecast(found, 18.52, 73.86)), "days")
asyncio.run(main())
PY
```

`_discover_provider` and `_select_forecast` are used rather than
`weather_forecast` because the tool appends `agromet_note`, which needs a real
`FarmerContext` — a stub with `deps=None` raises on `ctx.deps.skills` and the
failure looks like a weather bug when it is not.

Expect `discover` to name
`resource:weather-observation:mausamgram:daily-forecast` and `select` to raise
a 502 while the provider is down.

### The whole chat path

The model decides everything — which skill to load, whether to geocode, which
tool to call. Nothing here names a tool.

```sh
./scripts/pilot_chat.sh --lang en "what is the weather in Pune"
```

Flag ordering is fussy: `--lang en "question"`, not the other way round —
anything after the question is ignored. The script mints a ten-minute RS256
token from `private_key.pem` inside the container and streams one question
through `/api/chat/`.

To prove it really went over v2:

```sh
docker logs sva_app 2>&1 | grep "Weather v2"
# Weather v2 discover -> http://network-adapter:9201/consumer/discover
# Weather v2 discover: retrying without networkId (public catalog)
# Weather v2 discover -> http://network-adapter:9201/consumer/discover
# Weather v2 select   -> http://network-adapter:9201/consumer/select
```

Three discover lines, not one. The second is the retry the public-catalog
visibility problem forces — [plan.md](plan.md#the-discovery-visibility-problem).

---

## The cases worth running

| Ask | Expect |
|---|---|
| `"what is the weather in Pune"` | discover hits, select fails, unavailable message |
| `"what is the weather in London"` | asks for an Indian district; never reaches the network |
| `"weather in Nashik tomorrow"` | same as Pune — the tool takes no date |

Coordinates directly, for the ones a place name cannot express:

| Point | Why | Expect |
|---|---|---|
| 15.0, 88.0 | inside the box, in the Bay of Bengal | discovers — the box is a rectangle, not a coastline |
| 6.6, 68.1 | the box's SW corner | discovers |
| 51.5, -0.1 | London | discover returns nothing |
| 73.86, 18.52 | Pune with lat and lon swapped | discover returns nothing |

---

## Reading the result

| Answer | Means |
|---|---|
| A forecast | the provider is healthy again — tell someone |
| `Weather service unavailable. Please try again later.` | the provider failed; the adapter returned `NET_DOWNSTREAM_UNAVAILABLE` |
| `No weather forecast data found for the requested location.` | discover found no provider, **or** select returned zero days |
| `Weather request timed out.` | the provider hung rather than erroring |

The third row is doing two jobs, and that is the open bug — see the
`TODO(weather-v2)` in `agents/tools/weather_v2.py`.

---

## When it says unavailable

In rough order of likelihood right now:

1. **The provider has no data.** Current state. Check directly — if this
   returns `"fcstday1":{}`, nothing downstream can help:

   ```sh
   curl -s -H "Authorization: $MAUSAMGRAM_AUTH" \
     "https://mausamgram.imd.gov.in/nwpapi/get-daily?lat=18.52&lon=73.86&date=$(date +%F)"
   ```

2. **The provider is flapping.** Roughly one request in four succeeds. The
   adapter retries three times before giving up, so a run can fail purely on
   luck. Try again.
3. **The mapping is not sending `date`.** Omitting it is a guaranteed 500. The
   fix is written into the local helmcharts mapping but **is not published**,
   and the adapter fetches the published one. Until `MAUSAMGRAM_MAPPING_URL`
   points at a tag containing the fix, every select 500s.
4. **Nothing published.** `discover` returns an empty list silently. Check with
   a direct discover call before anything else.

---

## Switching back to v1

Drop `WEATHER_BECKN_VERSION` from `docker-compose.pilot.yml` and recreate the
app. `agents/tools/__init__.py` then imports the v1 module.

Unlike mandi, **this is not a transparent swap.** Both modules export the same
function name, signature and docstring, so the agent and the skill prompt do
not change — but the rendered answer does. The v2 provider mapping drops cloud
cover, the raw uncorrected temperatures, the compass form of wind direction and
the prose rain message, because the v0.1 schema pack has nowhere to put them.
[plan.md](plan.md) lists them.

v1 also needs the old BAP on port 8095, which is down — and it reads the same
IMD endpoint, so it would report the same lack of data anyway.
