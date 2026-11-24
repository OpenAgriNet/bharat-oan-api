# Soil Health Card: v1 -> v2

**Status: built and verified end to end on the local stack, 2026-11-24.** Behind
`SHC_BECKN_VERSION`, default `v1`, so nothing here serves farmers on v2 yet.

A farmer gives a phone number and a cycle year (`2024-25`); the answer is their soil
test reports, each with a link to the full card. Provider is the Soil Health Card
service (`shc`), capability `openagrinet:AgricultureResource`.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- Try it: [quick-try.md](quick-try.md)

---

## What the live provider actually does

Measured against SHC on 2026-11-24, with a working refresh token.

| Finding | Effect |
|---|---|
| One GraphQL URL for everything: a login query trades a refresh token for a 7-day access token, then the report query needs `Bearer <token>` | the adapter's token login learned a fixed JSON body and a header prefix |
| The login answers in about 0.15 s; the report query takes **15-26 s**, even for one report | the adapter's 15 s timeout failed every call; SHC now has 60 s and no retries |
| Ten reports are about 2.4 MB, mostly the HTML cards | fine under the adapter's limit |
| No token gives HTTP 200 with `errors: Not Authorized` and `data.getTestForAuthUser: null` | the tool treats `errors` or a missing list as "service unavailable" |

## How v2 works

```
publish (once)  cat-shc: one AgricultureResource, descriptor.code "shc-reports"
discover        descriptor.code "shc-reports"  ──► provider shc, its select operationRef
select          SHC's own GraphQL request in resourceAttributes ──► adapter (adds Bearer) ──► SHC
on_select       SHC's own GraphQL reply in resourceAttributes
```

- **Same tool, same arguments.** `check_shc_status(phone_number, cycle)` keeps v1's
  signature, so the prompts and skills do not change.
- **The query comes from SHC's OpenAPI.** The tool follows the resource's
  `actions[select].operationRef` and sends the request schema's `const` query.
- **v1's variables.** `limit 10`, `skip 0`, `locale en`, as provider-backend sent
  them; the OpenAPI gives no defaults.
- **The same text as v1.** The tool builds v1's own models from the reports and
  renders them, so the model sees what v1 showed it: the department, then per
  report "Soil Health Card for Farmer …", the card as markdown and a cached link.

## What the tool sends

```json
"resourceAttributes": {
  "@context": "<AgricultureResource v0.2 context>",
  "@type": "openagrinet:AgricultureResource",
  "query": "query GetTestForAuthUser(...) { getTestForAuthUser(...) { ... } }",
  "variables": { "phone": "+91XXXXXXXXXX", "cycle": "2024-25", "limit": 10, "skip": 0, "locale": "en" }
}
```

## Where v2 differs from v1

| Case | v1 | v2 |
|---|---|---|
| No reports for the phone and cycle | provider-backend answers 400; the farmer hears "service unavailable. Status code: 400" | "No data available." (decided 2026-11-24) |
| SHC answers with GraphQL `errors` | provider-backend answers 500 | "Soil health card status service unavailable." |

Everything else, including dropping a report that lacks `reportData` or
`rdfValues` fields, follows v1.

## What is built

| Repo | Branch | What |
|---|---|---|
| bharat-oan-api | `feat/shc-v2` | `agents/tools/v2/shc_scheme_status.py`, swapped in by `SHC_BECKN_VERSION=v2`; `agents/tools/v2/provider_openapi.py`, the OpenAPI reader shared with SATHI |
| network-adapter | `feat/shc-token-exchange` | `tokenRequestBody` and `tokenHeaderPrefix` for the `tokenHeader` scheme |
| helmcharts | `feat/shc-provider` | provider registration, `shc.yaml.tmpl`, the shared `AgricultureResource` step header, the select mapping, a per-provider timeout |

## What was verified

On the local stack, 2026-11-24: publish, discover and select through the adapter
returned 10 reports for one phone and cycle; the tool inside the app container
returned the same 10 reports, each with a cached card link, in 31 s. A unit test
checks that the v2 text equals v1's for the same reports.

## Open TODOs

1. **`DEFAULT_API_READ_TIMEOUT` must be 75 or more** wherever SHC runs on v2. The
   default is 10 s, which is shorter than SHC itself. It applies to every tool.
2. **Extended (L2) schema validation is off** for this flow, as for SATHI. It
   comes back on with every `make configure-adapter`.
3. **`@context` and `operationRef` point at network-specs `bv-features`** (raw
   GitHub URLs), as for SATHI.
4. **v1 SHC times out in prod today.** Seen in prod Langfuse on 2026-11-24:
   `tool:check_shc_status` took 10.29 s, the 10 s read timeout, and the farmer
   was told it "could not find" the card. The same fix as item 1.
5. **Answer quality is unchecked on prod's model.** Locally, on gpt-4.1-mini, the
   answer skipped the report link, mentioned downloading and named the department
   as the source, against the SHC prompt. Prod routes between Gemma and
   gpt-5.4-mini; recheck there. The tool's text equals v1's, so this is not a v2
   difference.
6. **The model asks for state and district** before calling the tool, seen in
   prod on v1. The SHC tool and prompt ask only for phone and cycle.
