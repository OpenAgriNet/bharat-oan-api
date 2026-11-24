# Open issues

Every open item across the v2 migration, in one place, for review. Nothing here
blocks what is already built and running on v2 — it is written down so it is
picked up deliberately rather than rediscovered.

Three sources feed this file:

- The numbered items below, which used to live in `README.md`.
- Each capability's own `plan.md`, linked at the bottom. Those carry the
  measurements and the reasoning; this file carries the one-line statement.
- Bugs found by running the agent with real farmer sentences. Those say
  plainly whether they are **ours** (the v2 port introduced them) or
  **pre-existing** (identical on v1), because the two need different owners.


**Waiting on access. No decision to make, no code to write.**

| # | Item | What unblocks it |
|---|---|---|
| 1 | KCC's AES key and IV rule are unknown | A real call. The mock and the codec share the same guess, so they agree whether or not it is right — [kcc/plan.md open item 1](kcc/plan.md#open-items). Check this first when credentials arrive |
| 2 | No KCC credentials | `KCC_ENTITY_TYPE`, `KCC_USER_ID`, `KCC_PASSWORD`. Swapping the mock is a `.env` edit and one `make register-provider PROVIDER=kcc UPDATE=1` — no code, no rebuild |
| 3 | `SATHI_API_KEY`, `SHC_TOKEN_URL`, `SHC_REFRESH_TOKEN` missing from `helmcharts/quick-start/.env` | Without them `setup.py` renders no provider binding for either, so the adapter answers `NET_ENTITY_NOT_FOUND` on SATHI's `select` and discover finds no `shc-reports` resource. This is the only reason the api collection is not green |

**Needs a decision.**

| # | Item | The question |
|---|---|---|
| 4 | The `AgricultureResource/v0.2` pack fix is on `bv-migration`; SATHI's catalog `@context` names `bv-features` | `bv-features` still has `required: [informationMode, subjectCategories]`, so deployed environments keep refusing every `select` for a catalog fact a select has no reason to carry. Needs a merge to `bv-features`, or the catalog repointed |
| 5 | The response side is not contract-driven | The request path now reads every field name from the provider's OpenAPI. Responses do not: `applicationNo`, `sanctionedAmount` and the rest are field names in the tool, in all six providers, because the mapping defines the response shape and nothing declares it. Closing it means new vocabulary — an `x-oan-to` or the mapping output described in the contract — across every provider, not a KCC change |
| 12 | Nothing says which of two providers to prefer | `discover` answers with an unranked list. Eight capabilities now share one selector, which takes the first candidate that can serve the call, skips the ones that cannot, and logs the pick when there was more than one — so a wrong choice is visible. What it still cannot do is prefer the *better* of two usable providers, because nothing in the payload ranks them. `mandi` is the one tool that does rank, by haversine distance, and `sathi_seed` sidesteps the question by returning every match. Settling it means discover returning an order, or the catalog carrying a preference the tool honours — a network-spec change, not a tool one |
| 13 | The agent fabricates "an OTP has been sent" on some phrasings | Three of eight KCC phrasings never call `initiate_kcc_otp` and reply as if they had, so the farmer waits for an SMS nobody asked for. Reproduces on v1, so it is a prompt bug, not a migration one. The `status_*.md` step 2 example spells out a finished sentence with a literal masked number and the model copies it. Fixing it means rewriting that step in all thirteen language files — [kcc/plan.md open item 12](kcc/plan.md#open-items) |
| 15 | A PM-KISAN grievance question is answered with the payment record | "any update on my PM Kisan complaint?" calls `initiate_pm_kisan_status_check` instead of `pmkisan_grievance_send_otp` in **5 of 6 runs**, then narrates the instalment record as *"your grievance status shows no issues blocking payment"* — a confident wrong answer, not a stall. `initiate_pm_kisan_status_check`'s description is byte-identical on v1 and v2 and says only "check the status of a farmer's scheme benefit", with no line excluding grievances. **Pre-existing, identical on v1.** Fix is an exclusion line in that description plus a routing line in `pm_kisan/status/*.md`, the guard KCC's prompt already has |
| 17 | `Grievance/v0.2` requires `grievanceOptions` even from a read-only resource | AIF reads support tickets and cannot file one, so it has no complaint categories to publish, and the pack refuses it: `SCH_REQUIRED_FIELD_MISSING: property "grievanceOptions" is missing`. Inventing codes to pass validation would put fiction in a catalog, so AIF's ticket resource stays on `AgricultureResource/v0.2`. The pack should require `grievanceOptions` only when `actions` advertises `support` — a `Grievance/v0.3` change, not a tool one — [aif/plan.md open item 7](aif/plan.md#open-items) |
| 16 | PMFBY invents the year and season | `check_pmfby_status_with_otp` was called with `year: "2026", season: "Rabi"` after the farmer gave only an OTP, so the farmer is told nothing was found for a season they never asked about. Neither version's description tells the agent to ask rather than assume. **Pre-existing, identical on v1.** `pmkisan_grievance_status` gained exactly the needed wording on v2 — "Ask the farmer for it; do not guess it" — and is the pattern to copy |
| 20 | ~~KHC's status read needs no OTP~~ | **Closed 2026-11-24. The gated pair is bound and the ungated read is left out on purpose.** The obstacle was never KHC's: `check-status` is a POST whose only input is a query parameter, and `BuildEndpoint` appended a query string for bodyless methods only. That is fixed in the adapter — a mapping names its query fields under the reserved `_query` key and `Split` lifts them onto the URL whatever the method (`util/query.go`), which is one function and two call sites, not a new transport. So KHC is asked to change nothing. `select` sends an OTP to the mobile the grievance was filed against and `status` answers it and reads the grievance back; a verify with no check-status before it returns 401, and that gate is why `GET /v1/state/grievances/status?grievanceNo=` stays unbound — it reads a grievance on a number alone, the farmer's own words included. Verified end to end against `mock-khc`, including the gate. **The adapter image must be rebuilt: tag `0.2.0` does not contain `Split`** — same note as item 22. What is still not KHC's published word: how long the status OTP lives and how many digits it has. The mapping states the filing challenge's figures — [khc/plan.md](khc/plan.md#the-status-journey-does-not-match-the-draft) |

| 21 | The agent invents a KHC grievance number | Driving the whole filing through `agrinet_agent.run` with ordinary farmer sentences, the agent answered *"Your grievance is registered. Grievance number: BR/231126/1"* having made **no tool call at all** — not `find_khc_location`, not `initiate_khc_otp`, not `register_khc_grievance`. One run printed the literal placeholder `BR/...`. The farmer is handed a number no desk recognises and told to quote it. **Pre-existing, identical on v1** — the same prompt and the same tool descriptions produced `BR/231124/1` with `KHC_BECKN_VERSION=v1`, so the transport is not the cause. It is also not every run: the same seven turns, differently worded, resolved all six IDs from real lookups and filed correctly. Same family as items 13 and 15 and wants the same fix — a line in the KHC step files forbidding a grievance number the tool did not return |

**Small and safe. Each is an hour or less.**

| # | Item |
|---|---|
| 6 | AIF's `_LOAN_OFFER` / `_TICKETS_OFFER` constants can read the offer off `_first_resource`, which now carries it |
| 7 | SATHI's `_FIELD_MEANINGS = {"cropCode": "crop", ...}` bridge can become `x-oan-from` hints in SATHI's OpenAPI — the same fix KCC just had |
| 9 | `mapping-files` and `spec-files` are started by hand and are not in `quick-start/docker-compose.yml`. Until they are, **never run `docker compose` with `--remove-orphans`** — it deletes them and `mock-ffs` |
| 10 | Stray probe catalogs are still in local discovery: `cat-aif-schemaapp-probe`, and `cat-khc-probe` and `cat-aif-probe` from the 2026-11-24 publish probes. All three are `isActive: false` and scoped to an audience nothing asks for |
| 18 | `AgricultureResource/v0.2` is missing from the publish module's `extendedSchema_contextRewrite`, so a resource pinning the canonical context is refused with `SCH_SCHEMA_ADAPTATION_FAILED: HTTP 404`. That is why AIF's ticket resource and SATHI's catalog carry raw `refs/heads/bv-migration` URLs while KCC, PMFBY and AIF's loan resource carry pinned ones. One line in `network-adapter.yaml.tmpl`, or merge `bv-migration` to `main` and delete the rewrite — overlaps item 4 |
| 19 | AIF's two contracts are still `openapi-draft.yaml`, so the `operationRef`s its catalog publishes 404 at every URL. `aif.py` never fetches them, so nothing is broken today. Promote both the way KCC's was: rename, add `x-oan-binding` per operation, replace `catalog-placeholder.json` with `catalog.json` and `index.md` |
| 11 | The PMFBY v0.2 mapping stubs are still `deployable: false` |
| 14 | **Ours, and a one-line fix.** `agents/tools/v2/aif.py` dropped the sentence v1 carries in `initiate_aif_otp`'s description — "The response carries the masked mobile number the OTP was sent to." Without it nothing tells the model the tool is the only source of that number, so it writes the "OTP has been sent to XXXXXX3457" line itself and never calls the tool: **0/8 runs on v2 against 2/8 on v1**. Put the sentence back and re-measure |

| 22 | `sessionBindAt` only read one payload shape, so the beneficiary check was silently skipped on the filing | **Ours, found and fixed.** `firstValueAt` passed the whole setting to `ValuesAt`, which splits on `.` only, so a comma-separated pair of paths resolved to nothing and the bind check — which fires only when the path reaches a value — never ran. KHC needs two paths because `confirm` carries the mobile on a contract and `support` carries it on a channel, and `support` admits no contract at all. Without it a transaction id alone was enough to file a grievance under another farmer's name. `firstValueAt` now iterates `splitList`, as `sessionKeyAt` already did, with a regression test. **The adapter image must be rebuilt wherever this is deployed: tag `0.2.0` does not contain it** |
| 24 | KHC returns two fields on the status read that its own contract does not declare | `currentLevel` and `createdAt` come back on every `verify-otp`, and `khc/tickets/v0.2/openapi.yaml` declares neither, so neither is mapped — a field the contract does not promise is not one a farmer's answer can rest on. `createdAt` has an obvious home (`case.filedOn`, which PMFBY already fills) and `currentLevel` has none: nothing in `Grievance/v0.2` says how far up a desk's escalation ladder a case has climbed. Either add both to KHC's contract and map them, or say in the contract that they are not read |
| 25 | The grievance number travels under two different names | `support` answers with `grievanceNo` on the support channel and `status` answers with `case.ticketNo` on the commitment, so a caller holding one answer cannot read the other's field. This is allowed rather than wrong: `GrievanceCall` is deliberately open (`additionalProperties: true`) and leaves operation-specific validation to the provider contract. It still means the agent carries the number between two shapes by hand. A `Grievance/v0.3` question, alongside item 17 |
| 23 | `_status` is dead in the deployed AIF, PMFBY and PM-KISAN mappings | The key is read nowhere the mapping reaches, so the branch it guards never runs. Nothing is broken today because every one of those mappings also tests `response.status` directly, but a reader takes the dead branch for the live one. Delete it, or wire it — not both |

Item 8 is done. `pmkisan_scheme_status.py` kept its own `post_action` and
`_first_resource`, and `pmkisan_grievance.py` imports both *through* that module
while passing `who=` — which the local copy never accepted, so all four of its
calls raised `TypeError` in committed code. Both duplicates are gone and the
shared ones in `aif.py` are used instead. The 266-assertion api collection could
not catch this: newman posts to the adapter and never enters the tool.


## Each capability's own open items

| Capability | Open items |
|---|---|
| KCC | [kcc/plan.md](kcc/plan.md#open-items) |
| AIF | [aif/plan.md](aif/plan.md#open-items) |
| PM-KISAN | [pmkisan/plan.md](pmkisan/plan.md#open-todos) |
| PMFBY | [pmfby/plan.md](pmfby/plan.md#open-todos) |
| SATHI | [sathi/plan.md](sathi/plan.md#open-todos) |
| SHC | [shc/plan.md](shc/plan.md#open-todos) |
| Weather | [weather/plan.md](weather/plan.md#open-questions) |
| Mandi | [mandi/plan.md](mandi/plan.md) |
| KHC | [khc/plan.md](khc/plan.md#open-items) — filing and status both ride the network, five actions on one binding, and the catalog carries the provider's real ten categories. Item 20 is closed; items 24 and 25 are what the status work left behind |
