# Mandi prices: v1 -> v2

**Status: done.** Running on the v2 network against the live Agmarknet
upstream, verified at tool level and through the full chat path.

This is both the record of what mandi cost and the template for the next
capability — the phases below are written as a recipe because they were walked
once, not because they were planned well.

- To run it: [quick-try.md](quick-try.md)
- To set up the stack first: [../stack-setup.md](../stack-setup.md)

Supersedes `documents/migration-pilot-plan.md`, which was written before any of
this ran and got two things materially wrong. Both corrections are below.

---

## What v2 changes

| | v1 | v2 |
|---|---|---|
| Which provider to call | hardcoded `BPP_ID` in env | discovery answers it |
| Request envelope | ~20 Python classes per tool | mapping YAML, network-side |
| Endpoint, timeout, retries | env vars | registry row |
| Credentials | in the tool | adapter auth profile |
| Pagination | 8 functions in the tool | adapter call plan |
| Date selection, formatting | in the tool | unchanged, stays in the tool |
| Prompt rules (13 languages) | markdown | unchanged |

One v1 `/search` becomes two v2 calls against **one** endpoint — the consumer
adapter branches on the action inside the payload:

    discover -> which providers can serve this, near here   (catalog)
    select   -> the actual answer from one of them          (live)

---

## Correction 1: the tool does not get smaller

The original plan predicted "about 60% of `mandi.py` goes away". Measured after
doing it:

| | v1 | v2 |
|---|---|---|
| Envelope / payload building | 160 | 155 |
| Pagination | 129 | 0 |
| Market picking + distance | 0 | 55 |
| v2 -> v1 field mapping | 0 | 63 |
| Tool function | 77 | 77 |
| **Total replaced** | **366** | **350** |

**4% smaller, not 60%.** Pagination genuinely disappeared. It was replaced by
two new jobs that v1 did not have, which brings us to the second correction.

Shared code — date helpers, `MandiItem`, `format_output` — is 297 lines and was
not touched at all. That is the reason v1 and v2 answers are comparable: both
render through the same formatter.

## Correction 2: work moves into the tool, not out of it

| Decision | v1 | v2 |
|---|---|---|
| Which mandi answers | provider | **tool** |
| Commodity name -> code | provider | **tool** |
| Nearest-mandi fallback | provider | **tool** |
| Ranking by distance | provider | **tool** |
| Fetching prices | provider | provider |
| Paging results | tool | gone |

Four jobs leaked from the provider into the AI tool, because `discover` returns
an **unranked** list. Someone has to pick from it, and right now that is the
tool.

This cuts against the three-layer split the migration is supposed to deliver —
adapter owns *how to call one provider*, discovery owns *who can serve this*,
AI owns *what to ask next*. **Fix this network-side before migrating the
remaining capabilities**, or all seventeen tools will reimplement the same
ranking loop.

---

## The recipe

Five phases. Mandi took all five; the next capability should be faster because
phases 0 and 2 are already done and shared.

### Phase 0 — network up (once, not per capability)

See [../stack-setup.md](../stack-setup.md). Only `register-provider` and
publishing the catalog repeat per capability.

### Phase 1 — prove the network path with no AI involved

1. Publish the capability's catalog entry (`capability-examples/<Capability>/publish/`)
2. `discover` — expect the published resources back
3. `select` — expect real data from the live upstream
4. Run the Postman suite as a regression check

Do not skip to Phase 3. Every failure found in mandi was found here, and they
are much harder to read once a model is in the loop.

### Phase 2 — AI layer against the network (once, not per capability)

Model config, keys and compose are in [../stack-setup.md](../stack-setup.md).
The one step that is **not** shared and must not be skipped:

5. **Record baseline answers on the v1 path, before changing anything.**
   Mandi skipped this and it is the single gap in its evidence.

### Phase 3 — swap the tool

8. Write the v2 tool as a **new module**, same function name, same signature,
   same docstring as v1
9. Pick between them at import time on an env flag
10. Replay the baseline questions through both and diff the rendered output
11. Confirm the status markers still mean what they meant

### Phase 4 — review before the next one

12. Write up what the capability actually cost and what leaked

---

## Why a second module and a flag

`agents/tools/__init__.py`:

```python
if os.getenv("MANDI_BECKN_VERSION", "v1").strip().lower() == "v2":
    from agents.tools.mandi_v2 import get_mandi_prices
else:
    from agents.tools.mandi import get_mandi_prices
```

Because both modules export the same name, signature and docstring, nothing
downstream can tell the difference: the agent, the manifest, the skill prompt
and the rendered answer are all identical. That is what makes the two versions
diffable output-against-output instead of by eye, and it means rollback is
unsetting a variable rather than reverting a commit.

Default stays v1 until a capability is signed off.

Reuse the v1 module's helpers by importing them rather than copying. In mandi,
`format_output` and the date logic are shared, so a difference in output can
only come from the transport.

---

## What mandi actually cost

Verified against the live network, all seven cases:

| Case | Result |
|---|---|
| Rahuri / Onion / 01–09 Sep range | 4 arrival dates, modal Rs. 3100 |
| Rahuri / Onion / single day | Rs. 500–5700, modal 3100 |
| Rahuri / Onion / day with no arrivals | `[Status: NO_DATA]` |
| Rahuri / Onion / latest available | 17 Nov 2026, modal Rs. 3000 |
| Pune / Onion / range | 7 arrival dates |
| Nashik (no market within 50 km) | `[Status: NO_DATA]` |
| Range over 30 days | `[Status: RANGE_TOO_LONG]` |

And through the full chat path, with the model choosing the skill, geocoding
the place name and calling the tool unprompted — the September question returns
the four arrival dates, with `discover` then `select` in the logs.

**Four startup failures, none obvious from the error.** Budget time for these
on the next capability; they are environment, not capability-specific:

1. The model registry validates *every* alias reachable from *every* use case
   at boot. One unrelated use case with no `base_url` stops the app.
2. The JWT public key is read at import time. No key, no boot, no bypass.
3. The quick-start adapter must be on `0.2.0`. On `0.0.1` it dies with
   `unrecognized step: checkCrossNetworkRequester`.
4. `discover` returns an empty list until the catalog is published, and nothing
   says so.

**One real bug, still open.** Agmarknet answers a date with no arrivals with
HTTP 400; no mapping rule reads that as an empty result, so the adapter reports
the upstream as unreachable and returns 502 `NET_DOWNSTREAM_UNAVAILABLE`. v1
gave the farmer `NO_DATA` for the same question, so the tool now swallows that
one NACK code to stay comparable. It is lossy — a genuine outage also reads as
`NO_DATA`. The fix belongs in the MandiPrice select action mapping.

**One gap in the evidence.** No v1 baseline was recorded, because the v1 BAP on
`host.docker.internal:8095` was not running. Phase 2 step 7 exists precisely to
prevent this; it was skipped and should not be on the next capability. v2 is
verified correct, but not verified identical to v1.

---

## What is still on v1

Seventeen tools still build a v1 payload and post it to the BAP:

```
advisory      agristack             aif           amul_vistaar
gfr           kcc                   khc           maha_vistaar
pmfby_grievance                     pmfby_scheme_status
pmkisan_grievance                   pmkisan_scheme_status
sathi_seed    search                shc_scheme_status
smam_scheme_status                  weather
```

Not all of them post to `/search`. The search-style tools do; `aif`, and
`kcc`/`khc` which import its `_endpoint`, post to `BAP_ENDPOINT/{action}` for
the full init / confirm / status set. Those are a bigger migration than weather
because v2 splits each action differently, not just search into discover and
select.

All seventeen are untouched by this work and need the v1 BAP running. With it
down they return `Connection refused` and the farmer sees "I could not get
that".

Note this affects mandi's own answer, which closes by offering a weather
forecast — that offer leads nowhere unless v1 is up.

## Next: weather (mausamgram)

The sensible second capability. It is already registered in the quick-start
(`make register-provider PROVIDER=mausamgram`, needs `MAUSAMGRAM_AUTH`), and
weather is the capability farmers ask for most after prices.

It should be cheaper than mandi: phases 0 and 2 are done, the flag pattern is
established, and `weather.py`'s formatter can be reused the same way mandi's
was.

Two things to settle before starting:

- **Record the v1 baseline first.** That means standing up the v1 BAP on 8095.
  It is the step mandi skipped, and it is the only way to show the migration
  did not change what the farmer reads.
- **Decide whether the ranking leak gets fixed first.** If discovery starts
  returning ranked results, the weather tool never grows the loop that mandi
  has. If not, weather gets its own copy of it.

---

## Not in scope

init / confirm / status capabilities, federation, the 14 "keep direct"
operations, moving prompt rules into catalogs, DSS.
