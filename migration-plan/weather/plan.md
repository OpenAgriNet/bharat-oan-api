# Weather forecast: v1 -> v2

**Status: planned, not started.**

Second capability. Provider is IMD Mausamgram (`mausamgram`), capability
`Weather Observation`, already registered in the quick-start and already
carrying publish / discover / select examples.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- The template this follows: [../mandi/plan.md](../mandi/plan.md)

---

## Why this one next

Weather is what farmers ask for after prices, the provider needs one
credential rather than four, and the capability examples already exist in
`helmcharts/quick-start/capability-examples/WeatherObservation/`.

It also tests something mandi could not. Mandi is a *list* capability — many
markets, pick one. Weather is a *point* capability — one provider, any
coordinate. If the v2 shape only fits list capabilities, this is where that
shows up.

---

## What is easier than mandi

**The ranking leak does not apply.** This is the important one, and it is the
reason weather should not wait for a discovery fix.

Mandi publishes one Resource per market, `discover` returns an unranked list,
and the tool grew 55 lines to pick from it. Mausamgram publishes **one
Resource for the entire IMD grid domain**. Its own catalog says so:

> One Resource, not one per place. The Provider forecasts on a 0.25 degree
> grid and accepts any coordinate inside its domain, snapping it to the
> nearest grid centre, so there is no enumerable list of locations to publish.

So `discover` returns a single resource, or nothing if the point is outside
the domain. There is nothing to rank, no nearest-provider fallback, no
name-to-code resolution. Three of the four jobs that leaked into the mandi
tool simply do not exist here.

**The arguments are simpler.** `weather_forecast(latitude, longitude)` — no
dates, no commodity. None of mandi's date-range logic, no `RANGE_TOO_LONG`
guard, no latest-available handling.

**No pagination to remove**, because v1 weather never had any.

---

## What is harder than mandi

**The formatter is welded to the v1 envelope.** Mandi had a clean
`format_output(items)` that the v2 module could import and reuse unchanged —
which is why its two versions are diffable output-against-output.

Weather has no such function. It renders with:

```python
weather_response = WeatherResponse.model_validate(data)
return str(weather_response) + await agromet_note(ctx, latitude, longitude)
```

`WeatherResponse` *is* the v1 Beckn envelope — Context, Catalog, Provider,
Item, Tag — and the human-readable text comes out of `__str__` methods hung
off those classes. There are 18 such classes in 430 lines.

Two options, decide in Phase 3:

1. **Map v2 into the v1 models and keep `__str__`.** Same trick as mandi's
   63-line field mapping. Output stays byte-identical by construction, which
   makes the baseline diff meaningful. Cost: the v1 envelope classes stay
   alive after v1 is gone, which is the thing the migration is meant to
   delete.
2. **Write a renderer against the v2 shape.** Cleaner end state, but the
   baseline diff stops being a byte comparison and becomes a judgement call
   about whether two differently-worded forecasts say the same thing.

Recommend (1) for the migration, with (2) as a separate follow-up once v1 is
switched off. Keeping the two changes apart means a wording difference can
never be confused with a transport bug.

**`agromet_note` is not part of this.** It reads Postgres directly
(`IMD_DB_URL`), not the BAP, so it is unaffected by v1 or v2 and should be
left exactly as it is. Worth stating because it is appended to every weather
answer and will show up in any diff.

---

## Steps

Phases 0 and 2 are done — the stack is up and the AI layer works. What remains
is weather-specific.

**Phase 0b — register and publish**

1. `make register-provider PROVIDER=mausamgram` — needs `MAUSAMGRAM_AUTH`
2. Publish `capability-examples/WeatherObservation/publish/ondemand-mausamgram.json`
3. Confirm `discover` returns the one resource for a point inside the domain,
   and nothing for a point outside it

**Phase 1 — prove the network path, no AI**

4. `select` with `capability-examples/WeatherObservation/select/select.json`,
   against the live Mausamgram upstream
5. Check the five-day forecast comes back with the six published parameters:
   Rainfall, Temperature, Humidity, WindSpeed, WindDirection, Alert

**Phase 2b — record the v1 baseline (do not skip)**

6. Stand up the v1 BAP on port 8095
7. Record v1 answers for a fixed set of points, through the chat path

This is the step mandi skipped, and it is why mandi can be shown correct but
cannot be shown unchanged. It is also the only phase here with an external
dependency, so start it first.

**Phase 3 — swap the tool**

8. `agents/tools/weather_v2.py` — same function name, same signature, same
   docstring as v1
9. `WEATHER_BECKN_VERSION` flag in `agents/tools/__init__.py`, defaulting to v1
10. Replay the baseline points through both paths and diff

**Phase 4 — review**

11. Write up what a point capability cost against what a list capability cost

---

## Test cases to fix before starting

Chosen to cover the ways a forecast question goes wrong. Fill in the expected
column from the v1 baseline in Phase 2b — that is what the baseline is for.

| Case | Why |
|---|---|
| A point well inside the IMD domain | the ordinary case |
| A point outside the domain | discover should return nothing, not an error |
| A point near the domain edge | grid snapping behaviour |
| A place name the farmer typed, through the chat path | geocode plus forecast together |
| Upstream down | should read as unavailable, not as "no forecast" |

That last one is the mandi bug in a different costume. Mandi could not tell a
provider outage from an empty result, and the tool now reports both as
NO_DATA. Check explicitly whether the weather mapping has the same gap
**before** writing the tool, not after.

---

## Open questions

1. **Does Mausamgram have an empty-result case at all?** A grid model answers
   every point inside its domain, so possibly not — in which case weather
   avoids the mandi bug entirely. Confirm in Phase 1.
2. **What does the v2 select return for a five-day forecast** — one resource
   with five days of attributes, or five resources? Changes the mapping, not
   the approach.
3. **Is the v1 BAP still runnable?** Everything in Phase 2b depends on it, and
   nobody has started it during this work.

---

## What this does not cover

The IMD advisory path (`imd_advisory.py`), which talks to Postgres and a
resolver rather than the network, and is out of scope for the Beckn migration.
