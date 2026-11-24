# GFR fertilizer recommendation: v1 -> v2

**Status: built and verified end to end on the local stack, 2026-11-24.** Behind
`GFR_BECKN_VERSION`, default `v1`, so nothing here serves farmers on v2 yet.

A farmer asks how much fertilizer to apply for a crop at a place; the answer is the
government (GFR) doses, worked out from the farmer's own soil test. Provider is `gfr`; the
data comes from the Soil Health Card service's GraphQL API.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- Try it: [quick-try.md](quick-try.md)

---

## What v1 does

Two separate `/search` calls, linked only by the model:

1. **Crop registry.** lat/lon goes to provider-backend, which finds the nearest state in its
   own database and calls `getCropRegistries(state)`. The model picks a crop id and state id.
2. **Recommendation.** provider-backend logs in, reads the farmer's **first** soil report
   (`limit: 1`) for n, p, k and OC, and calls `getRecommendations(state, results, crops,
   naturalFarming)`.

## How v2 works

```
publish (once)  cat-gfr:
                  gfr-advice       AgricultureAdvisory, one per state: SHC + LGD ids, boundary, GFR crops
                  gfr-soil-report  AgricultureResource, one, India-wide
discover        point + both codes, schemaContext naming both packs ──► the state + its crops + the soil report
select          gfr-soil-report {phone, cycle, limit 1} ──► adapter (Bearer login) ──► n, p, k, OC
select          gfr-advice {state, n/p/k/OC, crops, naturalFarming} ──► adapter ──► doses
```

- **The crop registry moved into the catalog.** Each state resource lists its GFR crops, so
  there is no registry call at chat time, and no nearest-state database.
- **Two packs, two selects.** The adapter picks a binding by provider + `@type`, so
  `gfr|openagrinet:AgricultureResource` and `gfr|openagrinet:AgricultureAdvisory` are separate
  registry entries, each with its own `select`. No adapter code change.
- **Same tools, same arguments.** `gfr_get_crop_registries` and `gfr_get_recommendations` keep
  v1's signatures and render with v1's own helpers, so the prompts and the text the model
  reads do not change.
- **The queries come from the OpenAPI.** Each resource's `actions[select].operationRef` points
  at network-specs `gfr/soil-report` or `gfr/advice`, and the tool sends that file's `query`.

## Where v2 differs from v1

| Case | v1 | v2 |
|---|---|---|
| Which state | nearest point in provider-backend's database | the state boundary the point falls in |
| Crop lines | season and irrigation columns filled | empty (still inside the crop name, e.g. `Barley (Rainfed / Rabi)`) |
| A point on a state border | one state | crops of every matching state, each line with its own state id |
| `district_id`, lat/lon on the recommendation | sent, then dropped by provider-backend | not sent |

Everything else follows v1: the first soil report only, at most 25 crop lines, at most 6
recommendation blocks, and v1's error texts.

## What is built

| Repo | Branch | What |
|---|---|---|
| bharat-oan-api | `feat/gfr-v2` | `agents/tools/v2/gfr.py`, swapped in by `GFR_BECKN_VERSION=v2`; v1's formatting and input checks shared from `agents/tools/gfr.py` |
| network-specs | `feat/gfr-soil-report-and-advice-openapi` (PR #29) | the soil-report and advice OpenAPI files |
| helmcharts | `feat/gfr-provider` | the `gfr` provider with two capabilities, its login, two mappings |
| network-adapter | — | none; uses the SHC token login (#146) |

## What was verified

On the local stack, 2026-11-24, for barley (Rainfed / Rabi) in Madhya Pradesh and one phone and
cycle: publish, discover, both selects through the adapter, and both tools inside the app all
worked. The doses (DAP 65.22, MOP 25.00, Urea 104.91 kg/ha) equal v1's answer for the
same farmer and crop.

## Open TODOs

1. **`DEFAULT_API_READ_TIMEOUT`.** The soil test is SHC's slow query, 15-26 s, longer than the
   10 s default. Same fix as the Soil Health Card.
2. **Extended (L2) schema validation is off** for this flow.
3. **Links point at branches**: the `@context` URLs, the `operationRef`s and the mapping URLs.
   To be moved to a fixed tag.
4. **Republishing does not drop a removed resource** in the local discovery service; the tool
   skips one whose code and pack do not match.
5. **Carried over from v1:** only the first soil report is used, so a farmer with several plots
   can get doses for the wrong one; the soil-test reply keeps all 12 soil values.
6. **v1's organic-inputs block is headed "Natural farming"** even on an inorganic answer, so
   the model may leave it out.
