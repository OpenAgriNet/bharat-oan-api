# Weather forecast: v1 -> v2

**Status: built and wired. Blocked on the provider, not on us.**

The tool, the renderer and the whole network path are done and exercised
end to end. What cannot be shown is a real forecast, because IMD Mausamgram
is currently returning no forecast data to anyone — see
[What the live provider actually does](#what-the-live-provider-actually-does).

Second capability. Provider is IMD Mausamgram (`mausamgram`), capability
`Weather Observation`.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- The template this follows: [../mandi/plan.md](../mandi/plan.md)

---

## What the live provider actually does

Measured against `https://mausamgram.imd.gov.in/nwpapi/get-daily` on
2026-11-19, with the real token. No mock was used at any point.

**1. `date` is required, and the mapping did not send it.**

The provider's own OpenAPI marks `date` optional. It is not. Omit it and every
request is a 500. Interleaved so both forms met the same service conditions:

| request | 200s |
|---|---|
| `lat`, `lon` | 0 / 20 |
| `lat`, `lon`, `date` | 5 / 20 |

This was a defect in `config/mappings/mausamgram/weather-observation.select.yaml`,
which sent only lat and lon. The published mapping at tag `0.1.0` — the one the
adapter actually fetched — was byte-identical to the local file, so the defect
was live for every request.

**Fixed.** The mapping now derives `date` from the current IST day, and it is
pushed to helmcharts `mandi-migration-pilot`. Pointing the adapter at it took
two steps, not one: `MAUSAMGRAM_MAPPING_URL` in `.env`, and then the registered
binding itself, because `make register-provider` is create-only and leaves an
existing `ProviderSchema` alone — see open question 6. Confirmed live in the
adapter log:

```
GET https://mausamgram.imd.gov.in/nwpapi/get-daily?date=2026-11-19&lat=18.52&lon=73.86 -> 200 OK
```

**2. The provider is about 25% available.** The second column above is not
noise — the same request, repeated, succeeds roughly one time in four. The
adapter already retries three times and then gives up.

**3. Even a 200 carries no forecast.** Every successful response had
`fcstday1..5` as empty objects and `lat_r`/`lon_r` as `null`:

```json
{"fcstday1":{},"fcstday2":{},"fcstday3":{},"fcstday4":{},"fcstday5":{},
 "abbreviation":{...},"location":{"lat":18.52,"lon":73.86},
 "lat_r":null,"lon_r":null}
```

True for every date tried, every coordinate tried, and for the other two
endpoints (`get-rtbc`, `get-rtma`) which never answered 200 at all.

**What this means.** Fixing `date` turns a 500 into a 200 that still has no
numbers in it. So the one thing this migration cannot yet demonstrate is a
rendered forecast from live data. Everything up to that point is demonstrated.
Re-run [quick-try.md](quick-try.md) when IMD is serving again; nothing in our
code needs to change for it to work.

---

## Same provider on both sides

v1 and v2 both end up at Mausamgram. The v1 tool asks for it by name —
`agents/tools/weather.py:346` sends `"name": "Weather-Forecast-Mausamgram"` —
and v2 registers the same upstream as the `mausamgram` provider.

| | Route | Provider |
|---|---|---|
| v1 | old BAP, port 8095 | Mausamgram |
| v2 | network adapter | Mausamgram |

Only the route changes. That makes the old-vs-new comparison a fair one: any
difference is ours, not a different data source.

**IMD is a separate thing and is not in scope.** `agromet_note`
(`agents/tools/imd_advisory.py:425`) reads an IMD Postgres database directly
via `IMD_DB_URL`. It never touches Beckn, so neither version affects it. Worth
stating because it is appended to every weather answer and will appear in any
diff.

---

## Why this one next

Weather is what farmers ask for after prices, the provider needs one
credential rather than four, and most of the network-side work is already done.

It also tests something mandi could not. Mandi is a *list* capability — many
markets, pick one. Weather is a *point* capability — one provider, any
coordinate. If the v2 shape only fits list capabilities, this is where that
shows up.

---

## What is already built

Checked, not assumed. These exist in `helmcharts/quick-start`:

| File | What it is |
|---|---|
| `capability-examples/WeatherObservation/publish/ondemand-mausamgram.json` | the catalog entry |
| `capability-examples/WeatherObservation/discover/discover.json` | a worked discover |
| `capability-examples/WeatherObservation/select/select.json` | a worked select |
| `config/mappings/mausamgram/weather-observation.select.yaml` | the full upstream mapping, both directions |
| `config/adapters/providers/mausamgram.yaml.tmpl` | auth config — one header token |

The mapping is the big one. For mandi we wrote that ourselves; here it is
already written, carefully, with its own reasoning recorded. Phases 0b and 1
are therefore verification, not construction.

---

## What is easier than mandi

**The ranking leak does not apply.** Mandi publishes one Resource per market,
`discover` returns an unranked list, and the tool grew 55 lines to pick from
it. Mausamgram publishes **one Resource for its whole grid domain**:

> One Resource, not one per place. The Provider forecasts on a 0.25 degree
> grid and accepts any coordinate inside its domain, snapping it to the
> nearest grid centre, so there is no enumerable list of locations to publish.

So `discover` returns a single resource, or nothing. Nothing to rank, no
nearest-provider fallback, no name-to-code resolution. Three of the four jobs
that leaked into the mandi tool do not exist here.

**The arguments are simpler.** `weather_forecast(latitude, longitude)` — no
dates, no commodity. None of mandi's date-range logic, no `RANGE_TOO_LONG`
guard, no latest-available handling.

**No pagination to remove**, because v1 weather never had any.

---

## What is harder than mandi

### The output cannot be byte-identical, and that is the main finding

For mandi we kept a single `format_output(items)` and compared the two
versions character for character. **That is not available here**, for two
independent reasons.

**First, the v2 mapping deliberately drops fields.** The mapping file lists
what it discards and why — the v0.1 schema pack has nowhere to put them:

| Dropped | Why |
|---|---|
| `cloud`, `cloud_message` | no `CloudCover` member in the parameter enum |
| `tmin_raw`, `tmax_raw` | no way to distinguish raw from bias-corrected |
| `rain_message` | prose restating `rain`, which is carried as a number |
| `wind` (compass form) | same information as `wdir` in degrees |
| `rain_icon`, `cloud_icon` | presentation |

If the v1 BAP passes any of these through as tags, v1's answer contains them
and v2's cannot. **This is the first thing to check in Phase 2b**, and it is
checkable the moment the v1 BAP is up — read one v1 response and see whether
cloud cover or the compass wind direction appear in the rendered text.

**Second, v1's renderer is generic.** `Item.__str__`
(`agents/tools/weather.py:163`) prints whatever tags arrive, as
`name: value`. It does not know about weather. So v1's output shape is
dictated entirely by what the old BAP chooses to send, and v2 sends a
different structure — one resource per forecast day, parameters grouped under
`values`.

**What this means for the plan.** The honest comparison is not a byte diff. It
is: *does the v2 answer contain every number the v1 answer contained, and are
the numbers the same?* Which fields are missing is then a decision to make
openly — drop them, or extend the schema pack.

Cloud cover is the one likely to matter to a farmer. Adding `CloudCover` to
the pack enum is described in the mapping as a one-line change.

### The formatter is welded to the v1 envelope

`WeatherResponse` *is* the v1 Beckn envelope — Context, Catalog, Provider,
Item, Tag — and the readable text comes out of `__str__` methods hung off
those classes. 18 classes, 11 `__str__` methods, 430 lines.

```python
weather_response = WeatherResponse.model_validate(data)
return str(weather_response) + await agromet_note(ctx, latitude, longitude)
```

Since byte-identical output is off the table anyway, **write a renderer
against the v2 shape.** The earlier recommendation here was to map v2 into the
v1 models to preserve the diff; that reasoning no longer holds, and mapping a
day-per-resource answer back into a tag list would be work done purely to
serve a comparison that cannot be exact.

Keep the renderer small and separate from the transport, so a wording
difference and a transport bug stay distinguishable.

---

## What the tool must send

From the mapping's precondition and request half:

- The request **must** carry `resourceAttributes.location.geo` with
  `type: "Point"`. Anything else is refused with
  *"this capability needs a Point location"*.
- Coordinates are GeoJSON order — **`[longitude, latitude]`**. Reversed, it is
  still a valid request for a point in the wrong place, so it fails as wrong
  data rather than as an error. Worth a test.
- **No date range.** `get-daily` takes lat and lon only and always answers its
  full horizon. Do not send `validity`.

---

## Steps

**Phase 0b — register and publish. Done.**

1. `make register-provider PROVIDER=mausamgram` — created the participant, the
   capability and the binding key
2. Published `capability-examples/WeatherObservation/publish/ondemand-mausamgram.json`
   — ACCEPTED, `catalog:weather-observation:mausamgram`, itemCount 1, geometry
   indexed at 6.5–37.5 N / 68–97.5 E
3. `discover` returns the one resource for a point inside the domain

**Phase 1 — prove the network path, no AI. Done, up to the provider.**

4. `select` reaches the live upstream. The adapter log shows the full chain:
   signature generated for `oan.dev`, base and extended schema validation
   passed against the v0.1 pack, binding key resolved to
   `https://mausamgram.imd.gov.in`, mapping compiled, outbound
   `GET /nwpapi/get-daily`
5. The provider answers 500. See
   [What the live provider actually does](#what-the-live-provider-actually-does)

So the network path is proven and the provider is not.

**Phase 2b — record the v1 baseline. Still not done.**

Unchanged and still deferred: it needs the old BAP on port 8095, which is down.
A warning to this effect is in the module docstring of
`agents/tools/weather_v2.py`. Note that the baseline is now doubly blocked —
even with the BAP up, v1 reads the same IMD endpoint, so it would record the
same empty answer.

**Phase 3 — swap the tool. Done.**

6. `agents/tools/weather_v2.py` — same function name, signature and docstring
7. A renderer written against the v2 shape, unit-checked against the shape the
   mapping documents, including an unknown parameter, a day with parameters
   missing, and the empty case
8. `WEATHER_BECKN_VERSION` in `agents/tools/__init__.py`, default v1
9. `WEATHER_BECKN_VERSION: v2` in `docker-compose.pilot.yml`

**Phase 4 — review.** Still to write, and worth writing once a forecast has
actually rendered.

---

## What was verified

Run on 2026-11-19 with `WEATHER_BECKN_VERSION: v2`, against the live stack and
the live provider.

| Case | Result |
|---|---|
| Pune, Delhi — inside the box | discover returns the resource |
| Bay of Bengal — inside the box, at sea | discover returns it, as designed |
| 6.6 N 68.1 E — box corner | discover returns it |
| London, Singapore — outside the box | discover returns nothing, no error |
| Coordinates swapped (73.86, 18.52) | lands outside the box, so it misses rather than answering about the wrong place |
| Provider down | **"Weather service unavailable."** — not "no forecast" |
| "what is the weather in Pune" through chat | geocoded, tool called over v2, unavailability relayed honestly, no invented numbers |
| "what is the weather in London" through chat | asks for an Indian district instead |
| Mandi, same build | all seven fixed cases unchanged; chat returns the four September dates |
| `date` fix live | registry repointed; adapter calls `get-daily?date=…&lat=…&lon=…` → 200 OK |
| 200 carrying zero forecast days | **"Weather service unavailable."** — not "no forecast" |
| discover `networkId` | sent as `"*"`; select stays `oan.dev`; one round trip each |

The swapped-coordinate row is weaker than it reads. It passes because 73.86 N
is outside the box, not because anything checks the order. A swap that stays
inside the box — 20 N 25 E reversed to 25 N 20 E, say — would still be a
confident answer about the wrong place. Only a provider that reported back the
cell it used could close that, and `lat_r`/`lon_r` are exactly that field.

The provider-down row is the one case the plan asked for that normally needs
contriving. The real outage supplied it.

---

## Scoping a discover to a catalog audience

Worth recording because it is not weather's problem — it is the next fifteen
capabilities' problem too, and it is easy to misread as a bug.

A catalog carries `visible_to`, a Postgres `text[]`. Discovery matches it with
`WHERE r.visible_to && $1::text[]` — an **array overlap**, not a pattern match.
`*` is an ordinary element in that array, not a wildcard the query expands. So
a public catalog literally holds the string `"*"`, and the way to see it is to
ask for `"*"`. Measured on this stack:

| caller sends | mandi catalog `{oan.dev}` | weather catalog `{*}` |
|---|---|---|
| `networkId: oan.dev` | found | not found |
| `networkId: "*"` | not found | found |
| no `networkId` | not found | found |
| `networkId: other.dev` | not found | not found |

Read as overlap, every row is what it should be. `networkId` is not "who I am"
— `senderId` is that. `networkId` on a discover is **which catalog audience to
look in**, and a caller that wants the public one says so.

The weather catalog publishes as `{*}`, which is correct for it: IMD forecast
data is open. It also could not be made otherwise if we wanted to —
re-publishing keeps the old visibility, a clean delete and re-publish still
lands on `{*}`, and sending `visibleTo` or `visible_to` in the publish body is
rejected by the schema as an unknown property. Worth knowing before a
capability is published with the wrong audience.

**What the tool does.** `_DISCOVER_NETWORK_ID` defaults to `"*"` and is sent as
`context.networkId` on discover only. `select` stays on the real network id —
it is routed by binding key and signed as this node, so an audience selector
would be meaningless there. One round trip either way.

**The one real gap.** The spec says `context.networkId` is
`oneOf: string | array` — `beckn.yaml:3088` even works an example of
discovering across two subnets with the array form. The local network adapter
rejects the array with HTTP 400. So a tool that wants both a public catalog and
a scoped one still has to ask twice. Weather does not need this today; a
capability with a mix of public and private providers will.

---

## Test cases

| Case | Why | Result |
|---|---|---|
| A point well inside India | the ordinary case | pass |
| A point outside the coverage box | discover should return nothing, not an error | pass |
| A point inside the box but not on land | see note below | pass — discovers, as designed |
| Coordinates swapped | must not quietly answer about somewhere else | pass, but weakly — see below |
| A place name typed by a farmer, through chat | geocode plus forecast together | pass |
| Upstream down | must read as unavailable, not as "no forecast" | pass, against a real outage |

**On the coverage box:** the published `coverageAreas` polygon is
68–97.5°E, 6.5–37.5°N — a **rectangle around India, not India's shape**. A
point in the Bay of Bengal or inside a neighbouring country still falls inside
it and will discover successfully. To test the outside case, go well clear of
the box.

**On the last row:** this is the mandi bug in a different costume, and weather
does **not** have it. Mandi reports a provider outage and a genuine empty
result both as NO_DATA. Weather separates them — the adapter returns
`NET_DOWNSTREAM_UNAVAILABLE` as a 502, the tool catches it as an
`HTTPStatusError` and says "Weather service unavailable", which is a different
sentence from "No weather forecast data found".

That separation is only as good as the adapter's error shape, and it had one
gap, since closed: a 200 carrying zero forecast days used to render as "no
forecast". For a grid model that answers every point in its domain that is far
more likely to be a failure, so the tool now reports it as unavailable too.
Only discover can honestly say "not here" — it is the thing that knows the
domain. Mandi deliberately does **not** do this: a market genuinely can have no
arrivals on a given day, so an empty answer there is ordinary. The difference
is the provider, not the protocol.

---

## Open questions

1. ~~**Does Mausamgram have an empty-result case at all?**~~ Answered, badly:
   it is returning empty for everything right now, so the empty case is not
   hypothetical and cannot currently be told apart from the broken case.
2. **Does v1's answer include the dropped fields?** Decides whether the schema
   pack needs a `CloudCover` addition. Still open, still Phase 2b — and now
   blocked twice over, since v1 reads the same dead endpoint.
3. **Is the v1 BAP still runnable?** Still unknown. Nobody has started it.
4. **TODO — re-run weather verification once IMD serves forecast data.**
   Blocks the one thing this migration has not shown: a forecast rendered from
   live bytes. Re-measured 2026-11-19, after the `date` fix: 4 of 12 selects
   answered (and each of those is already the adapter retrying three times),
   and **all four carried zero forecast days**. The API is reachable and
   authenticating; `fcstday1..5` come back as empty objects with `lat_r`/`lon_r`
   null, which reads like model output not loaded behind the API rather than a
   crashed service. Owned by IMD, not by us — v1 reads the same endpoint and
   would report the same.

   Re-checked 2026-11-20 **calling IMD directly**, no adapter, mapping or
   Beckn envelope in the path: 6 of 8 calls returned 200 (availability is
   improving), and every one carried the same empty envelope. The `abbreviation`
   block — rain, cloud, tmax, tmin, wind — comes back fully populated, so the
   service is alive and serving its static field dictionary; it is the model
   output that is missing, and `lat_r`/`lon_r` stay null, meaning it never even
   snaps the coordinate to a grid cell. Auth is fine: an unauthenticated call
   gets 401, ours get 200 or 500.

   That rules out everything on our side. Anyone calling this endpoint today
   gets the same, v1 included.

   To close it: re-run [quick-try.md](quick-try.md). If a forecast renders, no
   code change should be needed. If it renders *wrongly*, the renderer is the
   suspect — it is the only piece checked against the mapping's documented
   shape rather than against real bytes.

7. **TODO — the adapter rejects the spec's array form of `networkId`.**
   `context.networkId` is `oneOf: string | array` (`beckn.yaml:3088`), which
   even works an example of discovering across two subnets. The local adapter
   answers HTTP 400 to the array. Weather does not need it — one audience, one
   string. A capability with both public and private providers will, and
   without it each such tool has to send two discovers. Worth raising with the
   adapter team before that happens rather than after.
5. ~~**Who publishes the mapping fix?**~~ Done. Pushed to helmcharts
   `mandi-migration-pilot`, and the registered binding was repointed at it with
   `scripts/registry_set_mapping.py` — `make register-provider` could not,
   being create-only. Verified live: the adapter now calls
   `get-daily?date=…&lat=…&lon=…` and gets 200 instead of 500.
6. ~~**Should `setup.py` be able to update, not just create?**~~ Done.
   `register-provider` takes `--update` (`make register-provider
   PROVIDER=... UPDATE=1`), which PUTs the participant, binding and schema rows
   back from `.env` and prints every field that moved. Create stays the
   default, so a plain re-run is still a no-op; without `--update` the skip
   message now names the flag instead of just saying "already present".
   A participant carrying signing keys is refused rather than rewritten -- a
   new key osid would break every signature made with the old id.

   Verified live against the registry: a plain run skipped all three rows, an
   `--update` run with a matching `.env` reported nothing to correct, and a
   deliberate path change was written and then reverted, with the stored
   record keeping exactly the same fields throughout.

*(An earlier question — whether v2 returns one resource with five days or five
resources — is answered: the mapping returns one resource per forecast day.)*

---

## What this does not cover

The IMD advisory path (`imd_advisory.py`), which talks to Postgres and a
resolver rather than the network.
