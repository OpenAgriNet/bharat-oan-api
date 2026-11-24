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

None of that is executable. The design mappings under `network-specs` are
documentation of what a mapping would have to do; what the adapter actually
runs is a separate set of files in `helmcharts` — see the next section.

Nothing in `helmcharts` mentions KHC. No provider registration, no mock, no
`.env` keys. PMFBY and KCC both have a local mock; KHC has none.

The draft proposes `init`, `support` and `status`, and puts grievance creation
on **`support`** — a change from v1, which creates on `init`, and the right one:
both existing v2 grievance submissions use `support` too.

## How the three layers actually work

Written down because the first draft of this plan proposed a mechanism none of
them have.

**The schema** — `network-specs/schema/Grievance/v0.2/attributes.yaml`. An
`actions[]` entry may carry exactly `action`, `description`, `schema`,
`operationRef` and `attributesAt`, under `additionalProperties: false`. Two
entries with the same `action` are legal, because uniqueness is per item, but
**nothing in the entry distinguishes them** — there is no `variant`, `role` or
`operationId` field, and adding one is a pack change. The pack also carries
three things worth using: `scheme` (one per resource), `grievanceOptions` with
published `categories` and `subCategories`, and `challenge.methods` describing
the verification a desk requires.

**The adapter** — `helmcharts/quick-start/bin/setup.py` registers each
capability as a list of
`(action, native path, mapping file, timeout, retries, [method])` tuples, and
the mapping is JSONata in
`config/mappings/<provider>/<capability>.<action>.yaml` with `required`,
`request` and `response` blocks. The binding holds **one native path and one
mapping per action name**. A mapping can branch on the payload — AIF's
`loan.init.yaml` does, on `$exists($ca.challenge.response.value)` — but it
cannot change the path it posts to. `aif.yaml.tmpl` shows the other half: auth
and session rules are keyed by participant **and resource type**.

**The agent** — `agents/tools/v2/*.py`. `discover` by descriptor code, then
`post_action(action, body)`, with the request field names read from the
provider's OpenAPI at the catalog's `operationRef`.

## So how does a capability get two OTP steps?

Two ways, and which one applies is the provider's choice, not ours.

| Provider | Send and verify | Beckn actions |
|---|---|---|
| AIF | **One** endpoint, `/validate_beneficiaries/`, `otp` optional in the body | One `init`; the mapping branches on `challenge.response` |
| PMFBY insurance | **Two** endpoints, `/getOtp` and `/verifyMobile` | `init` to send, **`confirm`** to verify |

KHC has two endpoints, `/auth/send-otp` and `/auth/verify-otp`. So KHC follows
PMFBY: `init` then `confirm`. **No variants, no schema change, no adapter
change** — the earlier draft of this plan proposed all three and was wrong.

## The mapping, as far as it goes

| Native operation | Beckn action | Precedent |
|---|---|---|
| `POST /auth/send-otp` | `init` | PMFBY insurance |
| `POST /auth/verify-otp` | `confirm` | PMFBY insurance |
| `POST /v1/state/grievances` | `support` | PM-KISAN `:410`, PMFBY `:409` — both submit on `support` |
| `POST /v1/state/grievances/check-status` | `track` — **proposed** | none; see below |
| `POST /v1/state/grievances/verify-otp` | `status` | every grievance read |
| 7 × `GET /v1/state/reference/*` | **undecided** | none; see below |

Four are settled by precedent. The status send needs a fifth action name, and
the reference lists have no answer in the schema as published. Those two, plus
the farmer's token, are the real design work.

### The farmer's token, which the draft loses

`POST /auth/verify-otp` returns `data.token`. Nothing in the draft says where
that token goes next: every one of the twelve operations declares
`security: [service]`, the API key, and no second scheme. The v1 tool never
touches a token either, so the provider backend was either holding it or KHC was
not checking it.

It has to be checked — creating a grievance on a farmer's behalf without proof
they consented is the thing the OTP is for. So assume the token is required on
`POST /v1/state/grievances` until KHC says otherwise, and the adapter already
has the whole mechanism, from `aif.yaml.tmpl`:

```
sessionCaptureOn: confirm      # verify-otp hands back data.token
sessionSendOn: support         # create spends it
sessionTokenField: token
sessionKeyAt: "context.transactionId,context.transaction_id"
sessionBindAt: <the verified mobile>
```

The token is held in the adapter's cache against the transaction and never goes
back over the wire — the farmer's consent stays provider-side, which is the
point. **No new code.**

One snag the draft has to answer: AIF puts its service key in an `apiKey` header
and leaves `Authorization` for the farmer's bearer. KHC puts its **service key
in `Authorization`**, so the farmer's token needs a header of its own. The
adapter takes `sessionHeader` and `sessionPrefix` for exactly this, but only KHC
can say what the header is called.

### The fifth action

Checking a grievance needs its own OTP, sent to the mobile on the grievance
rather than the farmer's. That is a second send, and `init` is already spent on
registration. So the five native operations that are not reference lists need
five action names, and only four are precedented.

Two ways to find a fifth, and the session settings decide between them.

**One capability, five actions.** `init` (send), `confirm` (verify), `support`
(create), plus the status pair. `sessionCaptureOn` and `sessionSendOn` both
parse as **lists** (`splitList` in `session.go:92-93`), and both are per binding,
so one binding can capture on `confirm`, spend on `support`, and give the status
actions no token at all — which is what KHC wants, since its status OTP returns
the status directly and buys no session. This needs a fifth Beckn action name
for the status send; `track` reads naturally for "where has my complaint got
to". No OAN provider uses `track` today, so it would be the first.

**Two capabilities, each with its own `init`.** Nothing invented, but the
bindings are keyed by participant **and type** — `aif.yaml.tmpl` writes
`"__PARTICIPANT_ID__|openagrinet:Grievance"` — and both KHC capabilities are
`openagrinet:Grievance`, so they would collide on one key. AIF's two bindings
are two different types, and where it does want one session across both it says
so explicitly with `sessionBindingKey`. Two same-type capabilities on one
provider is untested and probably not supported.

**Take the first.** The session settings make one capability the natural shape,
and a new action name is a smaller ask than a second binding the adapter cannot
key. Confirm `track` is acceptable to the pack owners before writing the
catalog.

### The seven reference lists

They are `GET`s under `mappings/internal/` with `action: internal`, which is not
a Beckn action, and they appear in no `catalogCandidate.actions` entry. As
things stand no tool can reach them.

Three of the seven should not be fetched at runtime at all. **Schemes,
categories and sub-categories are catalog data**, and the Grievance pack already
has the field for them: `grievanceOptions.categories` and `subCategories`, which
is exactly how PM-KISAN publishes its ten codes and PMFBY its numeric ones.
Published that way, the farmer's complaint is matched against the catalog the
tool already holds from `discover`, and three network calls disappear.

The cost is that KHC's options are per scheme and per state, so the catalog
needs one resource per (state, scheme) pair — the pack's `scheme` is a single
object, so that split is forced anyway. How many that is depends on how many
states and schemes KHC covers, which we do not know. The endpoints are all
spelled `/v1/state/...`, which may mean one deployment serves one state.
**Count it before committing to this.**

That leaves the four location lists — states, districts, sub-districts,
villages. They are a hierarchy with a parent id at each level, they are far too
large to publish, and one `select` per capability means four capability entries
to reach four lists. There is no clean answer here in v0.2. The honest options:

1. **Four `select` capabilities.** Works today, nothing invented, and the
   catalog says plainly that KHC exposes four lookups.
2. **Ask for the location hierarchy to become its own capability** — a
   `Place`-style pack that any provider with its own gazetteer could publish.
   That is the right answer and it is a pack proposal, not this migration.

Take (1) and record (2).

## What this needs that does not exist yet

- **`parent_id` on the reference GETs.** The draft declares `parameters: []` on
  all seven and notes *"Legacy name/page filter the lookup; not native HTTP
  parameters"*. Districts without a `stateId` and villages without a
  `subDistrictId` cannot be what KHC really does, so the tracing is incomplete.
  This is the single most important thing to confirm against the live API.
- **`x-oan-request-attributes` on `KhcStatusStart`.** It takes `grievanceNo` as
  a query parameter, and `request_schema` reads only `x-oan-request-attributes`
  or `requestBody`, so the operation that starts the status journey is the one
  the contract reader cannot read.
- **`x-oan-from` hints on the write operations.** `KhcCreateRequest` names its
  ten required fields, which tells the tool what to fill but not where each value
  comes from: that `farmerMobileNumber` is the mobile verified in this session,
  that `scheme` and `division` must be taken from the same master row, that
  `externalReferenceNo` is the caller's own idempotency key.
- **The header the farmer's token goes in.** `verify-otp` returns `data.token`
  and every operation declares only the service API key. `Authorization` is
  already taken by that key, so the token needs its own header and the draft
  does not name one. Only KHC can answer it.
- **`track` accepted as a Beckn action** by the pack owners, for the status send.
  Nothing in `Grievance/v0.2` forbids it — `action` is just a non-empty string —
  but no OAN provider uses it, so it is a first.

## Implementation, in seven phases

Each phase is shippable and proves something the next one needs.

### 1. Settle the four contract questions

Not code: four answers, and the catalog cannot be written without them.

| Question | Who answers | Needs the API key |
|---|---|---|
| Do the reference GETs take a parent id, and what are their fields called? | KHC, or a probe | **yes** |
| Is the farmer's token required on create, and in which header? | KHC | no |
| Is `track` acceptable for the status send? | pack owners | no |
| Do schemes and categories fit in `grievanceOptions`? | counting rows | yes, to count |

Three of the four can be asked today.

### 2. The contract and the catalog

Promote `openapi-draft.yaml` to `openapi.yaml` with the three contract fixes
above, and write the catalog in place of `catalog-placeholder.json`: descriptor
code `khc-grievance`, type `openagrinet:Grievance`,
`AgricultureResource/v0.2`, five `actions[]` entries, `challenge.methods`
declaring OTP/SMS/6/NUMERIC, and `grievanceOptions` carrying the schemes and
categories if phase 1 says the volume allows it. One resource per (state,
scheme), because the pack's `scheme` is a single object.

The provider template `khc.yaml.tmpl` lands here too: the API key in the
provider block, and one binding capturing the session on `confirm` and spending
it on `support`.

### 3. `mock-khc` and the provider registration

Twelve endpoints modelled on `mock-aif`; a `khc` entry in `setup.py`'s provider
table needing `KHC_BASE_URL` and `KHC_API_KEY`; the mapping files under
`config/mappings/khc/`. Fixed OTP `123456` as `MOCKKHC_OTP`, and deterministic
negatives keyed off the input the way `mock-kcc` keys off the mobile's last
digit.

**The lists must carry realistic volumes.** Three villages per sub-district will
make any design look fine and prove nothing.

KHC writes to a real grievance desk, so unlike the other five capabilities the
mock is not a stand-in for absent credentials — it is where creation is tested
permanently.

### 4. The status journey

Two actions — `track` to send the OTP, `status` to read the result — with no
master data and no write. The smallest thing that proves phases 2 and 3 work
together, and the only journey that needs no session token. Every v1 guard stays — the
`[A-Za-z]{2}/[0-9]{6}/[1-9][0-9]*` check with its per-reason error text, the
six-digit OTP check, no automatic retry, and the wording that tells the agent
this is not the registration OTP.

### 5. Master data, cached

`agents/tools/v2/khc_master.py`: one `select` per location list, cached in Redis
through the aiocache instance in `app/core/cache.py` under a `khc-master`
namespace with a long TTL, failing open to a direct call when Redis is down —
the pattern `helpers/master_catalog.py` already uses. Without a cache the
four-level walk would make v2 slower than v1, which had the provider backend do
it in one hop. The name normalisation moves across from v1.

### 6. The two aggregating tools

`find_khc_location` and `get_khc_grievance_options` become local matching over
the cache and the catalog, producing the same `matches` / `needs` / `state` /
`schemes` / `match` structures the renderers already read. `_render_location`
and `_render_tree` move across **unchanged**: they are farmer-facing text whose
behaviour thirteen prompt files describe, and rewriting them is regression risk
for no gain. `lookup_khc_reference` becomes a read of the cache with the
50-per-page paging done as local slicing.

This is the bulk of the work. It is a provider's job arriving in our code.

### 7. Registration, then the flag

`initiate_khc_otp` on `init`, `verify_khc_otp` on `confirm`,
`register_khc_grievance` on `support` filling `KhcCreateRequest` from the
contract rather than from constants. Every v1 refusal stays, because each exists
for a reason: the ten-digit mobile check, `farmer_type` from the verified
`isRegistered` rather than from the model, the six-integers check,
`confirmed=True`, the `externalReferenceNo` derived from the session and the
payload, and no automatic retry after an unconfirmed submission.

Then `khc_service_note` moves to a version-neutral module — `agents/agrinet.py:55`
imports it from the v1 tool today, so flipping the flag would leave the agent
importing both files — and `KHC_BECKN_VERSION` goes into
`docker-compose.pilot.yml` defaulting to `v1`, with all seven tools imported as
one block, because the OTP binds the whole journey.

Verification is three layers reported separately: the newman folder, the tool
entry points, then the agent with real farmer sentences in Hindi and English.
The negatives that matter here are a village in no list, a scheme KHC does not
cover, a wrong OTP, an unknown grievance number, and an unconfirmed submission —
that last one checked for the absence of a retry, not for a message.

## What can start before the API details arrive

Everything except the reference-list question. The token header, the `track`
decision, the contract fixes, the catalog, the mock and the status journey need
nothing we do not have.

What the API key unlocks is narrow and decisive: whether the reference GETs take
a parent id, and what their fields are really called. The draft's `stateid` and
`statename` are guesses, and those seven responses feed the six master IDs that
`register_khc_grievance` refuses to send without. Reading them is read-only, no
mobile, no write.

## Open items

1. **Where does the farmer's token go?** `verify-otp` returns `data.token` and
   no operation declares a second security scheme, while `Authorization` is
   already carrying the service key. Creating a grievance without proving the
   farmer consented defeats the OTP, so assume it is required and ask KHC for the
   header name. The adapter needs no code — `sessionCaptureOn: confirm`,
   `sessionSendOn: support`, `sessionHeader`.
2. **Is `track` acceptable for the status send?** One capability needs five
   action names and only four are precedented. Costs nothing to ask and blocks
   the catalog.
3. **The reference GETs declare `parameters: []`.** A district list that takes no
   `stateId` cannot be what KHC does, so the tracing is incomplete. Needs the API
   key to settle, and until it is settled the master-data design is a guess.
4. **Do schemes and categories fit in the catalog?** `grievanceOptions` is the
   right home and would remove three network calls, but KHC's options are per
   state and per scheme, so the volume decides. Count before committing.
5. **No vocabulary for a location hierarchy, in any v0.2 pack.** Four `select`
   capabilities work and say the right thing in the catalog. A `Place`-style
   pack is the real answer and is a pack proposal, not this migration. Recorded,
   not taken.
6. **No KHC credentials and no mock.** Same position KCC was in, except KHC
   also writes. A mock is not optional here.
7. **Thirteen language files reference tool behaviour.** If any tool's
   contract changes — an argument added, an error reworded — all thirteen need
   the same edit. KCC's open item 12 is this problem already.
8. **`khc_service_note` runs on every turn of every conversation.** It is v1
   code in `agents/tools/khc.py` imported directly by `agents/agrinet.py`, so
   flipping `KHC_BECKN_VERSION` would leave the agent importing both files.
   Either the note moves somewhere version-neutral, or the v2 module re-exports
   it.

**Settled, recorded so they are not reopened:** creation goes on `support`, not
`init`, matching PM-KISAN and PMFBY. The two OTP steps are `init` and `confirm`,
matching PMFBY insurance, because KHC's send and verify are different endpoints.
`variant` keys in `operationRef` entries are **not** an option — `Grievance/v0.2`
sets `additionalProperties: false` on `actions[]` and the adapter binds one path
per action, so an earlier draft of this plan proposed something neither layer
supports.

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
