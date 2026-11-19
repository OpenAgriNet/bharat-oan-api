# Weather forecast: v1 -> v2

**Status: planned, not started.**

Second capability. Provider is IMD Mausamgram (`mausamgram`), capability
`Weather Observation`.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- The template this follows: [../mandi/plan.md](../mandi/plan.md)

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

Phases 0 and 2 are done — the stack is up and the AI layer works.

**Phase 0b — register and publish**

1. `make register-provider PROVIDER=mausamgram` — needs `MAUSAMGRAM_AUTH`
2. Publish `capability-examples/WeatherObservation/publish/ondemand-mausamgram.json`
3. Confirm `discover` returns the one resource for a point inside the domain

**Phase 1 — prove the network path, no AI**

4. `select` with `capability-examples/WeatherObservation/select/select.json`,
   against the live Mausamgram upstream
5. Check the five parameters come back grouped as the mapping describes, one
   resource per forecast day

**Phase 2b — record the v1 baseline (do not skip)**

6. Stand up the v1 BAP on port 8095
7. Record v1 answers for a fixed set of points, through the chat path
8. **List which fields v1 prints.** This is what tells us whether the dropped
   fields above are a real loss or a non-issue

This is the step mandi skipped, and it is why mandi can be shown correct but
cannot be shown unchanged. It is also the only phase with an external
dependency, so start it first.

**Phase 3 — swap the tool**

9. `agents/tools/weather_v2.py` — same function name, signature and docstring
10. A small renderer for the v2 shape
11. `WEATHER_BECKN_VERSION` flag in `agents/tools/__init__.py`, default v1
12. Replay the baseline points through both paths and compare field by field

**Phase 4 — review**

13. Write up what a point capability cost against what a list capability cost

---

## Test cases to fix before starting

| Case | Why |
|---|---|
| A point well inside India | the ordinary case |
| A point outside the coverage box | discover should return nothing, not an error |
| A point inside the box but not on land | see note below |
| Coordinates swapped | must not quietly answer about somewhere else |
| A place name typed by a farmer, through chat | geocode plus forecast together |
| Upstream down | must read as unavailable, not as "no forecast" |

**On the coverage box:** the published `coverageAreas` polygon is
68–97.5°E, 6.5–37.5°N — a **rectangle around India, not India's shape**. A
point in the Bay of Bengal or inside a neighbouring country still falls inside
it and will discover successfully. To test the outside case, go well clear of
the box.

The last row is the mandi bug in a different costume. Mandi could not tell a
provider outage from an empty result and reports both as NO_DATA. Check
whether the weather path has the same gap **before** writing the tool.

---

## Open questions

1. **Does Mausamgram have an empty-result case at all?** A grid model answers
   every point inside its domain, so possibly not — in which case weather
   avoids the mandi bug. Confirm in Phase 1.
2. **Does v1's answer include the dropped fields?** Decides whether the schema
   pack needs a `CloudCover` addition before we switch. Answer it in Phase 2b.
3. **Is the v1 BAP still runnable?** Everything in Phase 2b depends on it, and
   nobody has started it during this work.

*(An earlier question — whether v2 returns one resource with five days or five
resources — is answered: the mapping returns one resource per forecast day.)*

---

## What this does not cover

The IMD advisory path (`imd_advisory.py`), which talks to Postgres and a
resolver rather than the network.
