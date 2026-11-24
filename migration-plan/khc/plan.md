# KHC — Beckn v2 migration plan

Kisan Help Center is the grievance desk of last resort: any scheme without its
own complaint flow is routed here, and new AIF complaints are too. It is the
largest capability in the agent — `agents/tools/khc.py` is 787 lines against
KCC's 408 and AIF's 445 — and the only one that both **writes** to a provider
and carries a multi-step form.

Status: **not started.** Nothing is written yet. This plan is the reading of how
v1 works today and what v2 has no answer for. The network side has a design
draft from the September contract sweep; every mapping in it is
`deployable: false`.

## What a farmer gets today

Two journeys, seven tools, thirteen languages.

| Journey | Tools in order |
|---|---|
| Register a grievance | `find_khc_location` → `get_khc_grievance_options` → `initiate_khc_otp` → `verify_khc_otp` → `register_khc_grievance` |
| Check a grievance | `check_khc_grievance_status` (no OTP, sends one) → `check_khc_grievance_status` (with OTP) |
| Fallback for either | `lookup_khc_reference` — the raw paged lists, used when the two aggregating tools fail |

Registration asks for, in this order: scheme, state, district, village, the
problem in the farmer's own words, a matched sub-category, confirmation, mobile,
OTP, and — only when the OTP reply says `isRegistered=false` — name and gender.
`register_khc_grievance` refuses to send until all six master IDs are real
integers resolved from KHC master data, and until `confirmed=True`.

Two things only KHC has:

- **A per-turn system-prompt injection.** `khc_service_note` runs on every turn
  of every conversation (`agents/agrinet.py:55`), scanning the history for KHC
  words so the agent knows a KHC grievance is in progress. No other capability
  touches the system prompt.
- **An anti-fabrication check on the farmer's own words.** `_resolve_concern`
  refuses to look up categories unless the complaint text actually appears in
  something the farmer said in this chat. If the agent paraphrases, the tool
  sends it back to ask again.

## The two actions and eight request types

v1 sends everything through two Beckn actions, with a `request_type` tag
choosing the behaviour. The envelope is AIF's — KHC imports `_build_payload`,
`_endpoint`, `_descriptor`, `_outcome_tag` and `_values` from
`agents/tools/aif.py` and overrides the provider to `khc-agri` and the item to
`khc-grievance`.

| Action | `request_type` | What it does |
|---|---|---|
| `init` | `get_otp` | Send a registration OTP to a mobile the farmer gave |
| `init` | `verify_otp` | Verify it; the reply carries `isRegistered` |
| `init` | `find_location` | **One call** that resolves state, district, sub-district and village from a place name |
| `init` | `grievance_tree` | **One call** that returns the state's schemes, or a scheme's category/sub-category catalogue |
| `init` | `get_reference` | One raw list — states, districts, sub-districts, villages, schemes, categories or sub-categories — 50 per page |
| `init` | `create_grievance` | Submit, with an `externalReferenceNo` derived from the session and the payload |
| `status` | `get_status_otp` | Send an OTP to the mobile registered **on the grievance**, not the farmer's |
| `status` | `verify_status_otp` | Verify it and return the grievance record |

Eight behaviours, two actions. That is the shape the v2 port has to break apart.

## What the provider actually does

This is the finding that decides the size of the job.

`find_location` and `grievance_tree` are **not** provider endpoints. KHC's own
API has only flat paged lists. The aggregation — walk state to district to
sub-district to village, fuzzy-match the name, decide whether the answer is one
place or a list to show — happens in `bharat-provider-backend`'s
`khc.service.ts`, and comes back to the tool as tag values the renderers read:
`matches`, `district`, `needs`, `state`, `schemes`, `match`. `_render_location`
(line 155) and `_render_tree` (line 239) are 160 lines of turning those into an
instruction for the agent.

The v2 network adapter is a mapping engine: one Beckn action maps to one native
HTTP call. It has nowhere to put a fan-out across seven lists with pagination
and fuzzy matching. So in v2 that work has to be re-homed, and there are only
three places it can go:

1. **Into the tool.** `find_khc_location` becomes four `select` calls and the
   matching moves to Python. Honest, no network change, and it is the pattern
   mandi already set when ranking by distance moved into the tool. It also makes
   KHC the second tool doing a provider's job.
2. **Into KHC's own API.** Ask for aggregate endpoints. Not ours to schedule.
3. **Keep a KHC service in front.** Register the existing provider backend as the
   v2 provider and let the adapter call *it*, not KHC. Smallest change, but it
   keeps a v1-shaped component in a v2 network and the OpenAPI we publish stops
   describing KHC.

**This needs a decision before any code.** The rest of the plan assumes (1),
because it is the only option that does not depend on someone else.

## What already exists on the network side

`network-specs/api/external/khc/tickets/v0.2/`, from commit `5b5e7fe`:

- `openapi-draft.yaml`, 710 lines, `0.2.0-draft`, twelve native operations:
  two auth, seven reference lists, three grievance. Every operation carries an
  `x-source` line pointing at the exact line of `khc.service.ts` it was traced
  from. The server is `https://provider.example.invalid`.
- Twelve mappings, all `status: source-traced`, `deployable: false`, each
  saying *"Design only. The adapter must implement the described transport and
  guards before enabling this action."*
- `catalog-placeholder.json` — no catalog.
- Thirteen entries in `mappings-index.json` under descriptor code `khc-tickets`,
  and one integration note: *"Ticket submission and lookup are unverified."*

Nothing in `helmcharts` mentions KHC. No provider registration, no mock, no
`.env` keys. PMFBY and KCC both have a local mock; KHC has none.

The draft proposes `init`, `support` and `status`, and puts grievance creation
on **`support`** — a change from v1, which creates on `init`.

## Four gaps between the draft and the machinery

Each is small on its own. Together they are why this is not a copy of KCC.

**1. One action resolves to exactly one operation.**
`provider_openapi.operation_ref` returns the first catalog entry whose `action`
matches. KHC needs two `init` operations (send-otp, verify-otp) and two `status`
operations (check-status, verify-otp). The draft already writes a `variant` on
every action entry — `KhcStart`, `KhcVerify`, `KhcStatusStart`,
`KhcStatusRead` — so the intent was there; the resolver just ignores it.
Fix: `operation_ref(resource, action, variant="")`. Twelve lines, backwards
compatible, and it unblocks any future provider with two calls per action.

**2. The seven reference lists are not catalog actions.**
They sit under `mappings/internal/` and appear in no `catalogCandidate.actions`
entry. A v2 tool cannot reach them at all as things stand. Either they become
catalog actions with variants (`KhcRefstates` … `KhcRefsubcategories`), or
option (1) above is wrong and the aggregation stays provider-side.

**3. `check-status` has no request body.**
`KhcStatusStart` takes `grievanceNo` as a **query parameter**.
`request_schema` reads `x-oan-request-attributes` or `requestBody` and raises
`SchemaUnavailable` when there is neither. So the one operation that starts the
status journey is the one the contract reader cannot read. Fix: declare
`x-oan-request-attributes` on it, the same vocabulary KCC uses.

**4. The draft carries no OAN vocabulary at all.**
Zero `x-oan-request-attributes`, zero `x-oan-from`. `register_khc_grievance`'s
ten required fields are declared in `KhcCreateRequest`, which is enough for the
tool to learn the field *names* — but nothing tells it that `farmerMobileNumber`
is the verified mobile, that `scheme` and `division` must come from the same
master row, or that `externalReferenceNo` is the caller's own idempotency key.
Without `x-oan-from` hints the tool has to know those by heart, which is the
hardcoding the Sathi pattern exists to remove.

## What gets built

In `bharat-oan-api`:

- `agents/tools/v2/khc.py`. Seven tools, same names and signatures, so the
  skill manifest and all fifteen prompt files are untouched.
- Seven `_beckn_v2("KHC")` call sites in `agents/tools/__init__.py`, imported as
  one block — the OTP binds the whole journey, so a farmer cannot start on v1
  and finish on v2. Same reasoning as KCC.
- `KHC_BECKN_VERSION` in `docker-compose.pilot.yml`, default v1.
- `_render_location` and `_render_tree` move across unchanged if the renderers
  keep receiving the same structures; they are the farmer-facing text and
  rewriting them is a regression risk for no gain.

In `network-specs`:

- Promote `openapi-draft.yaml` to `openapi.yaml` with `x-oan-request-attributes`
  on the four write operations and `x-oan-from` hints where a field needs one.
- A real catalog, descriptor code `khc-tickets`, resource type
  `openagrinet:Grievance`, with `variant` on every action entry.
- Flip the twelve mappings to `deployable: true` once each is exercised.

In `helmcharts`:

- A `mock-khc` — twelve endpoints, same shape as `mock-aif`. KHC writes real
  grievances to a real desk; this cannot be tested against the live provider.
- Provider registration and the `.env` keys (`KHC_BASE_URL`, `KHC_API_KEY`).
- An api-collection folder with the usual positive and negative assertions.

## Order of work

1. Settle the aggregation question. Nothing else is safe to start.
2. `operation_ref` variants, with a test. Smallest change, unblocks the rest.
3. `mock-khc` and provider registration — the ability to run anything at all.
4. Promote the OpenAPI and write the catalog.
5. Status journey first: two operations, two tools, no master data, no write.
   It is the half that can be proved before the hard half starts.
6. Reference lists and the two aggregating tools.
7. Registration last, because it is the only irreversible call.
8. Verify three layers — newman, the tool entry points, then the agent with real
   farmer sentences in Hindi and English.

## Open items

1. **Where the aggregation lives.** Section *What the provider actually does*.
   Blocks everything.
2. **`support` versus `init` for creation.** The draft moves it; v1 creates on
   `init`. Pick one and make the catalog, the mapping and the tool agree.
3. **No KHC credentials and no mock.** Same position KCC was in, except KHC
   also writes. A mock is not optional here.
4. **Thirteen language files reference tool behaviour.** If any tool's
   contract changes — an argument added, an error reworded — all thirteen need
   the same edit. KCC's open item 12 is this problem already.
5. **`khc_service_note` runs on every turn of every conversation.** It is v1
   code in `agents/tools/khc.py` imported directly by `agents/agrinet.py`, so
   flipping `KHC_BECKN_VERSION` would leave the agent importing both files.
   Either the note moves somewhere version-neutral, or the v2 module re-exports
   it.

## Risks

- **It writes.** Every other migrated capability reads. A double submission is a
  duplicate complaint on a real desk, and `_UNCONFIRMED` exists because v1
  already hit this. The v2 port must keep `externalReferenceNo` derived from the
  session and payload, and must keep refusing to retry automatically.
- **Eight behaviours behind two actions is the most brittle shape in the
  codebase.** `_call`'s expected-outcome map is one dictionary deciding what
  counts as success for eight different calls. Splitting it per operation is the
  point of the port; getting it wrong silently turns a failure into a success
  message.
- **It is the fallback for every other scheme.** A KHC regression does not show
  up as "KHC is broken" — it shows up as every unsupported scheme question
  dead-ending.
