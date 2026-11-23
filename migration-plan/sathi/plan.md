# SATHI seed availability: v1 -> v2

**Status: built and verified end to end on the local stack, 2026-11-23.** Behind
`SATHI_BECKN_VERSION`, default `v1`, so nothing here serves farmers on v2 yet.

A farmer asks where to buy certified seed of a crop; the answer is the dealers in
their district holding stock. Provider is SATHI (`sathi`), capability
`openagrinet:AgricultureResource`.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- Try it: [quick-try.md](quick-try.md)

---

## What the live provider actually does

Measured against SATHI's stock API on 2026-11-21, with the real key.

| Finding | Effect |
|---|---|
| The live path is `/inv-apis2/stock/getSeedAvailability`; `/inv-apis/` answers **405** | the network-specs contract still says `/inv-apis/` |
| The key is accepted **only** as `apiKey` in the JSON body; header or query gives 400 | the adapter needed a new auth scheme (`authScheme: body`) |
| No stock is **HTTP 409** `{"statusCode":409,"status":"Error","message":"Data Not Found","data":[]}` | the adapter treated it as a failure (502) until `answerStatuses` |
| State and district are **LGD codes** (Pune `27`/`490`) | the catalog carries LGD codes |
| The crop list (`get-crops-list`) is national: 217 crops, no key needed | it moved into the catalog |
| Answers are large: Pune paddy has 658 lots, about 460 KB | the 4 MiB adapter limit is fine |

## How v2 works

```
publish (once)  cat-sathi: one AgricultureResource per Maharashtra district (36),
                each with all 217 crops, its LGD codes and its boundary
discover        point + descriptor.code "sathi-stock"  ──► the farmer's district
select          SATHI's own request fields in resourceAttributes ──► adapter ──► SATHI
on_select       SATHI's own reply in resourceAttributes (200 with lots, or 409)
```

- **No SATHI master API on v2.** v1 calls `get-crop-group` and `get-crops-list`
  directly; v2 reads the crop list from the discovered resource.
- **Location first, then crop.** The crop list arrives with the district, so v2
  asks in the opposite order to v1.
- **The farmer's own district.** v1 picks the nearest district point
  (`get_sathi_nearest_district` in provider-backend's DB); v2 picks the district
  the point is in. Near borders they can differ.
- **The select is built from SATHI's OpenAPI.** `list_sathi_crops` follows the
  resource's `actions[select].operationRef`, reads the request schema, and lists
  the fields for the model. `const` fields (`seedClass`) are set by the tool;
  `writeOnly` fields (`apiKey`) are left to the adapter.

## What the tool sends

```json
"resourceAttributes": {
  "@context": "<AgricultureResource v0.2 context>",
  "@type": "openagrinet:AgricultureResource",
  "cropCode": "A0102", "seedClass": "CERTIFIED I", "stateCode": "27", "districtCode": "487"
}
```

The answer comes back the same way: SATHI's `statusCode`, `status`, `message`
and `data` beside the same `@context` and `@type`. The tool renders `data` with
v1's own helpers, so the text matches v1.

## What is built

| Repo | Branch | What |
|---|---|---|
| bharat-oan-api | `feat/sathi-seed-v2` | `agents/tools/v2/sathi_seed.py`; skill `seeds.availability_v2` swapped in by `SATHI_BECKN_VERSION=v2` (manifest `replaces` / `enabled_by`); 13 v2 prompts |
| network-adapter | `feat/agriculture-resource-step` | `authScheme: body`, `answerStatuses`, the `AgricultureResource` step |
| helmcharts | `feat/agriculture-resource-sathi` | provider registration, `sathi.yaml.tmpl`, the select mapping, examples |

With the flag unset, the catalog, system prompt and tools are exactly v1's.

## What was verified

On the local stack, 2026-11-23:

| Question | Result |
|---|---|
| Paddy in Nashik | dealers in Nashik; same dealers, bags and ranking as prod v1 |
| Paddy in Pune | discover matched Ahilyanagar and Pune; the model passed `district`, Pune chosen |
| Barley in Nashik | SATHI 409, shown as "not available" |
| Paddy in Hyderabad | refused by the prompt's Maharashtra rule, as v1 |

The tool alone also corrected a state and district passed as names to their
codes, and refused a wrong district code without calling SATHI. The API key
appeared 0 times in the adapter logs.

## Test cases

| Case | Expect |
|---|---|
| Point in a small district | one district, dealers or "not available" |
| Point in a large district near another (Pune city) | asks which district, or uses `district` if given |
| Point outside Maharashtra | v1's Maharashtra sentence |
| Crop not in the district's list | refused before any SATHI call |
| SATHI 409 | v1's "Data is not available…" text |
| OpenAPI file unreachable | v1's service-error text |

## Open TODOs

1. **`@context` and `operationRef` point at network-specs `bv-features`** (raw
   GitHub URLs), because v0.2 is not on GitHub Pages yet.
2. **Extended (L2) schema validation is off** for this flow; SATHI's fields are
   checked only by the mapping's `required:` check and the tool's own checks.
3. **Large districts match by bounding box** in the discovery service, so a point
   can return two districts. The tool asks the farmer.
4. **Field meanings live in code** (`_FIELD_MEANINGS`). Planned: hints in the
   OpenAPI request schema (`x-oan-from`, `x-oan-choose`, `x-oan-filled-by`) so
   code copies exact values and the model only picks the crop.
5. **Raise with network-specs:** the `/inv-apis2/` path, the 409 no-stock answer,
   and that their mapping files use a format the adapter cannot load.
6. **The catalog builder and its OpenStreetMap boundaries** live outside these
   repos; OSM data needs a credit line, not yet decided.
7. **Native-speaker review** of the flow lines in the 12 translated v2 prompts.

## What this does not cover

Stock outside Maharashtra (v1's prompt limits SATHI to Maharashtra) and seed
classes other than `CERTIFIED I` (v1 always sends it).
