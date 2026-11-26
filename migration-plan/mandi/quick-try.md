# Trying the mandi use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what
changed and why, see [plan.md](plan.md).

Mandi-specific setup, on top of the shared steps:

- `make register-provider PROVIDER=agmarknet` — needs base URL, token URL,
  access name and password
- Publish `helmcharts/quick-start/capability-examples/MandiPrice/publish/ondemand-mh.json`
- `docker-compose.pilot.yml` carries `MANDI_BECKN_VERSION: v2`. Remove it and
  the same questions run on v1.

---

## Two ways to ask, and the difference matters

### The tool on its own

Calls `get_mandi_prices` directly. No model involved, so a failure here is a
network or adapter failure, not a prompt problem. This is the one to run first.

```sh
docker exec -e PYTHONPATH=/app sva_app python scripts/pilot_mandi_check.py
```

Seven fixed cases, chosen to cover the ways a price question goes wrong:

| Case | Expected |
|---|---|
| Rahuri / Onion / 01–09 Sep | 4 arrival dates, modal Rs. 3100 |
| Rahuri / Onion / single day | Rs. 500–5700, modal 3100 |
| Rahuri / Onion / day with no arrivals | `[Status: NO_DATA]` |
| Rahuri / Onion / latest available | most recent arrival date |
| Pune / Onion / range | 7 arrival dates |
| Nashik — no market within 50 km | `[Status: NO_DATA]` |
| Range over 30 days | `[Status: RANGE_TOO_LONG]` |

Your own question:

```sh
docker exec -e PYTHONPATH=/app sva_app python scripts/pilot_mandi_check.py \
  --place "Rahuri, Maharashtra" --commodity Onion --from 01-09-2026 --to 09-09-2026
```

`--place` is geocoded the same way the chat path geocodes it, and the script
prints the coordinates it resolved to. **Include the state.** Bare district
names are ambiguous in OpenStreetMap — "Ahmednagar" alone lands several hundred
kilometres away, which then reads as "no data" rather than as a bad lookup.

You need `--commodity` plus either `--place` or both `--lat` and `--lon`. Give
it less and it says so rather than running the seven fixed cases at you.

Flags: `--lat --lon --place --commodity --from --to --nearby --show-payloads`.
`--show-payloads` prints the discover and select request bodies, which is the
fastest way to see what the tool actually asked for.

### The whole chat path

The model decides everything — which skill to load, whether to geocode, which
tool to call. Nothing here names a tool.

```sh
./scripts/pilot_chat.sh --lang en "onion price in Rahuri from 1 to 9 September"
```

Expect the four September arrival dates. Flag ordering is fussy:
`--lang en "question"`, not the other way round — anything after the question
is ignored.

The script mints a ten-minute RS256 token from `private_key.pem` inside the
container and streams one question through `/api/chat/`.

To prove it really went over v2:

```sh
docker logs sva_app 2>&1 | grep 'agents.tools.v2.mandi'
# Mandi v2 discover -> http://network-adapter:9201/consumer/discover
# Mandi v2 select   -> http://network-adapter:9201/consumer/select
```

---

## Reading the result

Three status markers, unchanged from v1:

| Marker | Means |
|---|---|
| `[Status: NO_DATA]` | discover found no market, or select returned nothing |
| `[Status: NO_DATA_AT_REQUESTED_MANDI]` | answered from a nearby market instead |
| `[Status: RANGE_TOO_LONG]` | over 30 days; refused before any network call |

---

## When it says no data

In rough order of likelihood:

1. **Nothing published.** `discover` returns an empty list silently. Check with
   a direct discover call before anything else.
2. **The place is further from a market than it looks.** The search radius is
   50 km. Nashik is 99 km from Rahuri APMC, which is why it is in the test
   cases as a deliberate miss.
3. **That date genuinely had no arrivals.** Markets do not trade every day.
   Try a range rather than a single day.
4. **The provider is down.** Currently indistinguishable from case 3 — see the
   open bug in [plan.md](plan.md).

---

## Switching back to v1

Run without `docker-compose.pilot.yml`:

```sh
docker compose -f docker-compose.yml up -d
```

`agents/tools/__init__.py` then imports the v1 module. Nothing else changes —
both modules export the same function name, signature and docstring, so the
agent, the skill prompt and the rendered answer are identical either way.

Note that v1 needs the old BAP running on port 8095. With it down, every mandi
question returns "Mandi service unavailable" — as do weather and the other
sixteen v1 tools.
