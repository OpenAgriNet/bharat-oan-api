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
- `api/external/khc/catalog-placeholder.json` — one level up, and not a
  catalog: it names the pack (`Grievance`) and points at the draft.
- Twelve entries in the shared `api/external/mappings-index.json`, all under
  descriptor code `khc-tickets`, with one integration note:
  *"Ticket submission and lookup are unverified."*

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

## What a publish request looks like

One POST to the adapter's `/publish` mount, which strips the path and routes
through the network module to discovery
(`routing-network-adapter-provider.yaml`). The action is `catalog/publish`,
and the whole catalog goes in one request. AIF's, trimmed, is the model
(`helmcharts/quick-start/capability-examples/AgricultureResource/publish/ondemand-aif.json`):

```json
{
  "context": {
    "domain": "agriculture",
    "action": "catalog/publish",
    "version": "2.0.0",
    "transactionId": "…", "messageId": "…", "timestamp": "…",
    "networkId": "oan.dev", "senderId": "oan.dev", "receiverId": "oan.dev"
  },
  "message": { "catalogs": [ {
    "id": "cat-khc",
    "bppId": "oan.dev",
    "bppUri": "https://net-a.oan.dev",
    "descriptor": { "code": "KHC", "name": "Kisan Help Center" },
    "provider": { "id": "khc", "descriptor": { "code": "KHC", "name": "Kisan Help Center" } },
    "isActive": true,
    "resources": [ {
      "id": "res-khc-grievance",
      "descriptor": {
        "code": "khc-grievance",
        "name": "KHC grievance registration",
        "shortDesc": "Register a grievance with the Kisan Help Center and read its status."
      },
      "resourceAttributes": {
        "@context": "https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/schema/AgricultureResource/v0.2/context.jsonld",
        "@type": "openagrinet:AgricultureResource",
        "informationMode": "OnDemand",
        "subjectCategories": ["Scheme"],
        "agricultureSubjects": [ { "descriptor": { "code": "KHC", "name": "Kisan Help Center" } } ],
        "languages": ["en", "hi"],
        "coverageAreas": [ { "codeScheme": "ISO-3166-1", "areaLevel": "Country",
                             "areaCode": "IN", "areaName": "India" } ],
        "actions": [
          { "action": "init",
            "operationRef": "https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/api/external/khc/tickets/v0.2/openapi.yaml#/paths/~1auth~1send-otp/post" },
          { "action": "confirm", "operationRef": "…#/paths/~1auth~1verify-otp/post" },
          { "action": "support",  "operationRef": "…#/paths/~1v1~1state~1grievances/post" }
        ]
      }
    } ]
  } ] }
}
```

Four things in there are not obvious and all four are load-bearing:

- **`@context` is a `raw.githubusercontent.com` URL on `bv-migration`, not
  `openagrinet.github.io`.** github.io does not serve v0.2 at all, and the
  adapter resolves the pack from this URL. A github.io v0.2 URL fails with
  `SCH_SCHEMA_ADAPTATION_FAILED`.
- **`operationRef` is a JSON pointer with `/` escaped as `~1`**, and the schema
  pattern requires the document be named `openapi.yaml` — so the draft has to be
  promoted before anything can be published.
- **One catalog, many resources, told apart by `descriptor.code`.** AIF puts its
  loan and ticket capabilities in `cat-aif` as `aif-loan-status` and
  `aif-support-tickets`. That is what the tool's `discover` filter matches on.
- **`networkId` picks the audience.** AIF, mandi and weather publish to `*` and
  their tools discover on `*`; the scheme catalogs publish to `oan.dev`. A tool
  that asks on the wrong one matches nothing and reports "no data" — the bug
  that hid mandi's broken discover until 2026-11-23.

## Two `@context`s, and KHC is a Grievance

`@context` means a different thing in the catalog than it does in a request, and
confusing the two is the easiest mistake here. AIF carries **both**, and so will
KHC.

| Where | `@type` | `@context` | Why |
|---|---|---|---|
| The published catalog resource | `openagrinet:AgricultureResource` | `raw.githubusercontent.com/…/bv-migration/schema/AgricultureResource/v0.2/context.jsonld` | the catalog has to validate against a pack whose `@context` is not pinned |
| Every action payload — `init`, `confirm`, `support`, `select`, `status` | **`openagrinet:Grievance`** | `openagrinet.github.io/…/schema/Grievance/v0.2/context.jsonld` | this is the real capability, and it is the registry binding key |

AIF's own `status-aif.json` is exactly this: `@type: openagrinet:Grievance` with
the github.io `@context`, against a catalog published as
`openagrinet:AgricultureResource`.

**So what is KHC, in pack terms? A Grievance.** Not a `SchemeApplication` —
nobody applies to the Kisan Help Center, they complain to it, and
`SchemeApplication` is for reading an application's progress. The
`AgricultureResource` line in the catalog is a publishing workaround, not a
statement about what KHC is.

**How the github.io 404 is survived on the request side but not the catalog
side.** `extendedSchema_contextRewrite` on the adapter rewrites *where a pack is
fetched from*, never the payload's `@context`:

```
https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/|https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/schema/Grievance/v0.2/
```

So a request may carry the pinned github.io URL and still resolve. It is set only
on the blocks with `extendedSchema_enabled: "true"` — the transaction path. The
publish path has no such rewrite, which is why the catalog cannot name
`Grievance/v0.2` and AIF publishes as `AgricultureResource/v0.2` instead.

Both go away when `bv-migration` merges to `main` and github.io serves v0.2.
Until then KHC inherits the workaround as-is — **no new adapter keys**, because
the Grievance rewrite line AIF added already covers KHC.

## One resource, two `@type`s — not two resources

The two rows above are **one resource**, described twice in two different
documents. They are not two catalog entries, and `SchemeApplication` is not
involved in KHC at all.

```
catalog (published once)              action payload (sent on every call)
──────────────────────────────        ──────────────────────────────────
res-khc-grievance                     message.contract.commitments[0]
  descriptor.code: khc-grievance        .commitmentAttributes
  resourceAttributes:                      "@type": "openagrinet:Grievance"
    "@type": "openagrinet:                 "@context": github.io/…/Grievance/v0.2/
              AgricultureResource"         "scheme": {...}
```

The tool finds the resource by `descriptor.code` from `discover`, then posts a
call whose `@type` is the capability — and that `@type` is what keys the registry
binding, `khc|openagrinet:Grievance`.

AIF has two resources because it has two capabilities: a loan application and a
support-ticket list. KHC has one capability, grievances, so **one resource** —
plus a second only if the reference lists become a capability of their own.

### Why the catalog says the generic one

`GrievanceFields` pins `@context`:

```yaml
  GrievanceFields:
    required: [scheme, '@context', '@type', informationMode, grievanceOptions, actions]
    properties:
      '@context':
        oneOf:
        - const: https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/context.jsonld
        - type: array
          contains: {const: https://openagrinet.github.io/.../Grievance/v0.2/context.jsonld}
```

Both branches require a URL github.io answers with 404, and the root `Grievance`
is `allOf: [GrievanceFields]`. On the request side the adapter's
`contextRewrite` makes that survivable; on the publish side there is no rewrite,
so the catalog has to name a pack whose `@context` is not pinned. That is
`AgricultureResource/v0.2`. The identical wall AIF hit on
`SchemeApplication/v0.2`, solved the identical way — and there is no `Grievance`
publish example anywhere in `helmcharts`, which is the same fact from the other
side: nothing on this stack has ever published a Grievance catalog.

### What the workaround costs

`grievanceOptions` is a `Grievance/v0.2` field, and `AgricultureResourceFields`
requires only `informationMode` and `subjectCategories` and does not define it.
The root does not set `unevaluatedProperties: false`, so an extra
`grievanceOptions` may well pass unvalidated — but an attribute the pack does not
define is not published data, it is a field that happens not to be rejected.
**Test it before relying on it; assume schemes and categories come back as
`select` reads.**

Same for `scheme` and `challenge.methods`. The farmer-facing consequence is
small — the tool knows KHC needs a six-digit OTP because the tool is KHC's — but
the catalog cannot say so, and a per-(state, scheme) resource split has no
`scheme` field to split on. One resource per capability, not per scheme.

## So how does a capability get two OTP steps?

Two ways, and which one applies is the provider's choice, not ours.

| Provider | Send and verify | Beckn actions |
|---|---|---|
| AIF | **One** endpoint, `/validate_beneficiaries/`, `otp` optional in the body | One `init`; the mapping branches on `challenge.response` |
| PMFBY insurance | **Two** endpoints, `/getOtp` and `/verifyMobile` | `init` to send, **`confirm`** to verify |

KHC has two endpoints, `/auth/send-otp` and `/auth/verify-otp`. So KHC follows
PMFBY: `init` then `confirm`. No variants and no adapter code — though
`confirm` does need one line in the Grievance pack, which no Grievance provider
has ever needed because none has used it. The earlier draft of this plan
proposed a variant mechanism instead, and was wrong.

## The mapping, as far as it goes

| Native operation | Beckn action | Precedent |
|---|---|---|
| `POST /auth/send-otp` | `init` | PMFBY insurance |
| `POST /auth/verify-otp` | `confirm` | PMFBY insurance |
| `POST /v1/state/grievances` | `support` | PM-KISAN `:410`, PMFBY `:409` — both submit on `support` |
| `POST /v1/state/grievances/check-status` | `select` | mandi, weather — but not yet in the Grievance call map |
| `POST /v1/state/grievances/verify-otp` | `status` | every grievance read |
| 7 × `GET /v1/state/reference/*` | **blocked** | none; see below |

Four are settled by precedent. `confirm` and `select` both need a line in the
Grievance pack's call map before they will validate. The seven reference lists
have no answer the adapter can express at all, and that — not the missing
credentials — is what stops registration migrating.

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

### The fifth action, and what the adapter will actually route

Checking a grievance needs its own OTP, sent to the mobile on the grievance
rather than the farmer's. That is a second send, and `init` is already spent on
registration. So the five native operations that are not reference lists need
five action names, and only four are precedented.

Before choosing one, two gates nobody mentioned:

**The adapter routes exactly six actions.**
`routing-network-adapter-consumer.yaml` lists `discover`, `select`, `init`,
`confirm`, `support`, `status` and nothing else, and the tool posts to
`/consumer/<action>` (`aif.py:109`). Anything else is a one-line edit to that
file — small, but it is not free.

**The pack validates per action, and `Grievance/v0.2` knows three.**

```yaml
x-oan-schema-by-action:
  init: GrievanceCall
  status: GrievanceCall
  support: GrievanceCall
```

No `confirm`, no `select`. `SchemeApplication/v0.2` and
`AgricultureResource/v0.2` both list `confirm` and `select`, which is why
PMFBY's `confirm` works — it is on `SchemeApplication/v0.2`. PMFBY's *grievance*
is `v0.1` and uses only `support` and `status`. **No provider has ever sent
`confirm` on a Grievance capability.** Without the entry the payload falls back
to the root schema, which requires `scheme`, `grievanceOptions` and `actions` —
catalog facts no OTP reply carries — and is refused. That is the same failure the
README records AIF fixing for the other packs.

So the fifth action costs:

| Candidate | Routed today | In the Grievance call map | Exercised in OAN |
|---|---|---|---|
| `confirm` | yes | **no — one line needed** | PMFBY, on `SchemeApplication/v0.2` |
| `select` | yes | **no — one line needed** | mandi and weather, heavily |
| `track` | **no — one line needed** | **no — one line needed** | nowhere |

**Take `select` for the status send, not `track`.** Both need the same pack
line, but `select` is already routed and already carries real traffic through
the whole adapter path, while `track` would be the first of its kind in the
network. `track` reads better — "where has my complaint got to" — so it is worth
raising with the pack owners, but it buys semantics at the price of being first,
and this is a migration.

That gives one capability, five actions: `init` (send registration OTP),
`confirm` (verify it), `support` (create), `select` (send the status OTP),
`status` (read the grievance). `sessionCaptureOn` and `sessionSendOn` both parse
as **lists** (`splitList`, `session.go:92-93`) and are per binding, so one
binding captures on `confirm`, spends on `support`, and leaves `select` and
`status` with no token — which is right, since the status journey verifies
against the grievance, not the farmer's session.

**Why not two capabilities, each with its own `init`.** The bindings are keyed by
participant **and type** — `aif.yaml.tmpl` writes
`"__PARTICIPANT_ID__|openagrinet:Grievance"`, and discovery.md says it plainly:
*"registry bindings are keyed `<provider>|<@type>`"*. Both KHC capabilities would
be `openagrinet:Grievance` and collide on one key. AIF's two bindings are two
different types, and where it wants one session across both it says so with
`sessionBindingKey`.

### The seven reference lists — the one thing that does not fit

They are `GET`s under `mappings/internal/` with `action: internal`, which is not
a Beckn action, and they appear in no `actions` entry. As things stand no tool
can reach them. Working out where they should go is what turned up the hard
limit in this whole plan.

**The catalog cannot hold the options.** The earlier draft of this section said
schemes, categories and sub-categories are catalog data and belong in
`grievanceOptions`. They are, and they would — but `grievanceOptions` is a
`Grievance/v0.2` field and KHC publishes under `AgricultureResource/v0.2`,
because `Grievance/v0.2` pins `@context` to a github.io URL that 404s. See
*Which pack KHC publishes under* above. So all seven stay runtime reads.

**And the adapter binds one fixed path per (capability, action).** Two facts,
both checked:

- `ActionPlan.Path` is a plain `string` (`pkg/model/model.go:114-116`). No
  templating, no substitution from the payload. The JSONata mapping shapes the
  body and nothing else.
- A binding is keyed by participant and capability, and the capability is read
  off the payload's `@type` —
  `capabilityCodeAt: "message.contract.commitments[].commitmentAttributes.@type"`
  in `network-adapter.yaml`, and `ensure_actions_binding(participant,
  capability, …)` in `setup.py:647`. discovery.md states it outright:
  *"registry bindings are keyed `<provider>|<@type>`"*.

So seven native paths need seven distinct (`@type`, action) pairs. One `select`
on an `AgricultureResource`-typed capability reaches exactly **one** of the seven
— `sathi_seed.py:41` is the precedent, `@type: openagrinet:AgricultureResource`
with `select`. Reaching all seven needs seven `@type`s, and the only ones that
exist mean mandi prices, weather, facilities, knowledge, scheme applications and
grievances. None of them means "village list", and inventing one is a pack
change we do not get to make.

The ways out, in the order they should be tried:

1. **Ask KHC for one parameterised reference endpoint** —
   `GET /v1/state/reference?type=villages&parentId=…`. One native path, one
   `select`, one binding, all seven lists. It is a single request to the
   provider and it is the only option that leaves v2 clean.
2. **Keep the provider backend in front of the lists only.** Register
   `bharat-provider-backend` as the v2 provider for a `khc-reference`
   capability and let it keep doing the fan-out, while grievance filing and
   status go straight to KHC. Hybrid, ugly, and it works today.
3. **Propose a `Place`-style pack** for a location hierarchy any provider with a
   gazetteer could publish. The right answer, and far too slow to block on.

**Until one of these lands, the registration journey cannot migrate.** Filing
needs six master IDs and there is no supported way to fetch them. The status
journey has no such dependency, which is why it is phase 4 and registration is
phase 7.

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
- **`confirm` and `select` in `Grievance/v0.2`'s `x-oan-schema-by-action`.** It
  lists `init`, `status` and `support` only. Without the two extra lines pointing
  at the existing permissive `GrievanceCall`, a `confirm` payload is validated
  against the root schema — which requires `scheme`, `grievanceOptions` and
  `actions` — and refused. Two lines, and the same revision AIF already started.
- **One parameterised reference endpoint**, per *The seven reference lists*. This
  is the one gap that blocks a journey rather than inconveniencing it.

## Implementation, in seven phases

Each phase is shippable and proves something the next one needs.

### 1. Settle the four questions

Not code: four answers, and the catalog cannot be written without them.

| Question | Who answers | Needs the API key |
|---|---|---|
| Will KHC expose **one** parameterised reference endpoint? | KHC | no |
| Is the farmer's token required on create, and in which header? | KHC | no |
| Do the reference GETs take a parent id, and what are their fields called? | KHC, or a probe | **yes** |
| Will the pack take `confirm` and `select` in its call map? | pack owners | no |

Three of the four can be asked today, and the first one decides whether
registration can migrate at all.

### 2. The contract and the catalog

Promote `openapi-draft.yaml` to `openapi.yaml` with the contract fixes above,
and write the catalog in place of `catalog-placeholder.json`. One catalog,
`cat-khc`, with resources told apart by `descriptor.code` the way AIF's are:

| resource | descriptor.code | action-payload `@type` | actions |
|---|---|---|---|
| `res-khc-grievance` | `khc-grievance` | `openagrinet:Grievance` | `init`, `confirm`, `support`, `select`, `status` |
| `res-khc-reference` | `khc-reference` | `openagrinet:AgricultureResource` | `select` |

Both publish as `@type: openagrinet:AgricultureResource` with a
`raw.githubusercontent.com` `@context` on `bv-migration`. The second resource
exists only if question 1 is answered yes.

Note the descriptor code changes: the draft says `khc-tickets`, v1's item id is
`khc-grievance`. Pick `khc-grievance` and update the draft, so the catalog, the
index and the tool agree.

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

Two actions — `select` to send the OTP, `status` to read the result — with no
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

The whole status journey, end to end: the contract fixes, the catalog, the
provider template, `mock-khc` and phases 2 through 4. Nothing in it needs a
credential or a field name we do not have.

Registration cannot start, and not because of the missing key — because of the
reference endpoint. See *The seven reference lists*.

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
2. **`confirm` and `select` are missing from `Grievance/v0.2`'s call map.**
   It knows `init`, `status` and `support`. Two lines pointing at the existing
   `GrievanceCall` fix it; without them those payloads are validated against the
   catalog schema and refused. `track` was the earlier proposal for the status
   send and is dropped — it is also absent from the adapter's six-endpoint
   routing list, where `select` already is.
3. **The reference GETs declare `parameters: []`.** A district list that takes no
   `stateId` cannot be what KHC does, so the tracing is incomplete. Needs the API
   key to settle, and until it is settled the master-data design is a guess.
4. **Seven reference lists, and the adapter binds one fixed path per
   (capability, action).** `ActionPlan.Path` is a plain string and bindings are
   keyed `<provider>|<@type>`, so seven native paths need seven `@type`s and
   none of the existing ones means "village list". **This blocks registration,
   not just inconveniences it.** Ask KHC for one parameterised reference
   endpoint; fall back to keeping the provider backend in front of the lists.
5. **`grievanceOptions`, `scheme` and `challenge` cannot be published.** All
   three are `Grievance/v0.2` fields, and `Grievance/v0.2` pins `@context` to a
   github.io URL that 404s — the same wall AIF hit on `SchemeApplication/v0.2`.
   KHC publishes as `AgricultureResource/v0.2`, which defines none of them. A
   `Place`-style pack for location hierarchies is the real long answer and is a
   pack proposal, not this migration.
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
