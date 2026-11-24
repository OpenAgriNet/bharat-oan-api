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

## The native contract, verified

Read off `bharat-provider-backend/src/services/khc/khc.service.ts` on
2026-11-24 -- the code that calls KHC in production today -- and checked line
by line against the draft. **The draft is accurate.** All twelve operations,
their methods, their parameters and their response field names match. Nothing
below was probed and no credential was used.

The `REFERENCES` table at `khc.service.ts:31` is the whole answer:

| List | Parent query parameter | Id field | Name field |
|---|---|---|---|
| `states` | *(none)* | `stateid` | `statename` |
| `districts` | `stateId` | `districtid` | `districtname` |
| `sub-districts` | `districtId` | `subdistrictid` | `subdistrictname` |
| `villages` | `subDistrictId` | `villageid` | `villagename` |
| `schemes` | `stateId` | `scheme` | `schemename` |
| `categories` | `scheme` | `problemcategory` | `categoryname` |
| `sub-categories` | `problemCategory` | `problemsubcategory` | `subcategoryname` |

Four things in that table are load-bearing and three of them contradict an
earlier draft of this plan:

- **The parent parameter is camelCase and the response fields are lowercase.**
  `stateId` goes out, `stateid` comes back. They are not the same string and a
  mapping that reuses one for the other silently returns nothing.
- **KHC does not paginate and does not filter by name.** `getReference`
  (`khc.service.ts:324`) fetches the entire list in one GET and does the name
  match and the 50-per-page slice *in the provider backend*
  (`matches.slice((pageNumber - 1) * 50, pageNumber * 50)`). So the `page` and
  `name` arguments v1's tools pass were never KHC parameters. **v2 loses
  nothing by moving that work into the tool** -- it was never provider-side in
  the first place, which removes the main objection to option 1 in *What the
  provider actually does*.
- **The grievance options are a cascade, not a flat list.** `schemes` is
  scoped to a state, `categories` to a scheme, `sub-categories` to a category.
  So *"categories and sub-categories are catalog data"* is only half true: a
  published `grievanceOptions` is valid for exactly one scheme, which means a
  Grievance resource per `(state, scheme)` pair rather than one for KHC. That
  is affordable -- every such resource is `openagrinet:Grievance` and they all
  share **one** binding, because bindings are keyed `<provider>|<@type>` and
  resources are told apart by `descriptor.code`. Resource count does not cost
  routing. What decides it is how many pairs there are, which is the one
  question source cannot answer.
- **The service key is sent as `Authorization: ApiKey <key>`**
  (`khc.service.ts:146`), so the prefix is literally `ApiKey `, not `Bearer `.
  `KHC_BASE_URL` must be `https`, with no query or fragment, and the client
  uses a 20s timeout and refuses redirects.

Every reply is `{"status": "200 OK", "data": ...}` and `status` is checked as a
**string** against `/^20[01](?:\s|$)/`, not as a number. A reply without `data`
is treated as a failure whatever the HTTP code.

### The status journey does not match the draft

The draft publishes two operations for reading a grievance:

```
POST /v1/state/grievances/check-status?grievanceNo=…   (KhcStatusStart)
POST /v1/state/grievances/verify-otp                   (KhcStatusRead)
```

**Neither string appears anywhere in `khc.service.ts`.** What the provider
backend actually does is one call:

```
GET /v1/state/grievances/status?grievanceNo=…          (khc.service.ts:384)
```

and the dispatcher's `status` branch ignores `request_type` entirely --
`} else { result = await this.getStatus(text(values, "grievanceNo", 100)); }`
at `:532`. So `get_status_otp` and `verify_status_otp`, the two request types
v1's `check_khc_grievance_status` sends (`agents/tools/khc.py:693-694`), both
land on the same read. **In this revision the status OTP is never sent and
never checked.** The tool asks the farmer for a code that does not exist, and
the grievance was already returned by the first call.

**The draft was written against a different revision, so it cannot settle
this either way.** Its `x-source` lines point into a longer file than the one
on disk: `KhcCreate` cites `khc.service.ts#L588` and the snapshot is **577
lines**. `KhcStatusStart` and `KhcStatusRead` cite `#L568` and `#L576`, which
here fall inside the `catch` block that formats a failure response, and
`KhcVerify` cites `#L384`, which here is `getStatus`'s request line. The line
numbers are consistently shifted, not individually wrong, which is what reading
a `blob/dev` link against an older or trimmed checkout looks like. So the draft
is evidence about a revision this repository does not contain -- it neither
confirms the two status endpoints nor is caught inventing them.

One objection to the draft does survive, because it is about the adapter rather
than the provider:

- `KhcStatusStart` is declared `POST` with `grievanceNo` as its only input, in
  `query`, and no `requestBody`. **The adapter cannot call that.**
  `BuildEndpoint` returns early for any method `HasBody` accepts -- `GET`,
  `HEAD` and `DELETE` are the only ones it appends a query string for
  (`util/request.go:20-27`, `util/endpoint.go:28-31`) -- so a POST's mapped
  request becomes the body and the query is dropped. The operation as written
  would post to `/check-status` with no `grievanceNo` at all.

The verified `GET …/status?grievanceNo=` has neither problem: it is a GET, so
the adapter builds the query from the mapped request, and it needs **one**
action slot rather than two.

**Caveats, stated plainly.** This checkout is a snapshot dated 21 October 2026
with no git history. By date it is the newer of the two, but by content it is
the shorter, so "newer" does not follow -- it may be a trimmed or
differently-configured deployment rather than an earlier commit. Its
`khc.service.spec.ts` tests none of `get_status_otp`, `verify_status_otp`,
`find_location` or `grievance_tree`, and the dispatcher implements neither
`find_location` nor `grievance_tree` -- both would hit
*"Invalid KHC request_type."* at `:522`. So either the deployed backend is
newer still, or two of v1's seven tools are already broken in production.
**Not resolvable from this repository**, and it decides whether the v2 status
journey has an OTP at all -- which is a question about who may read a
farmer's grievance, not a question about plumbing. Carried as open item 11.

#### Resolved 2026-11-24, against the provider rather than the snapshot

Read-only probes against KHC UAT settled it, and they settled it against this
section. **All three operations exist.** A `GET` against `check-status` or
`verify-otp` answers 405, not 404, so the two the draft published are deployed;
the snapshot that names neither is older than what is running. The draft was
right and the backend on disk was the wrong witness.

So the gated pair is what is bound:

```
POST /v1/state/grievances/check-status?grievanceNo=…   select
POST /v1/state/grievances/verify-otp                   status
```

The one objection that survived -- that the adapter could not call a POST whose
only input is a query parameter -- was an adapter limit and is fixed as one. A
mapping now names its query fields under the reserved `_query` key and `Split`
lifts them onto the URL whatever the method (`util/query.go`), which replaced
`queryParameters` plus `BodyFields` with one function and two call sites. KHC is
asked to change nothing.

`GET /v1/state/grievances/status?grievanceNo=` is **left unbound on purpose**,
and that is the part worth being plain about. It reads a grievance on a number
alone -- 404 for a number no desk holds, so the call reaches a grievance lookup
and not an authorization check -- and the reply carries the free text the farmer
wrote. The gated pair returns the same body behind an OTP, so binding the
ungated one would publish a read with no gate and buy nothing. v1 prompts for a
code before reaching it, but the dispatcher's `status` branch ignores the
`request_type` that prompt sets, so on this revision that prompt gated nothing.

### What this changes

The reference lists are each one GET with at most one query parameter and no
paging. That is the simplest thing the adapter can express, and it means the
seven lists are not blocked on contract work at all -- only on the action-slot
arithmetic in *The seven reference lists*, which is unchanged: seven paths,
and `ActionPlan` is a `map[string]ActionPlan` keyed by action name
(`pkg/model/model.go:94`, `sunbirdRegistry/providerrecord.go:335`), so two rows
with the same action name silently overwrite. Confirmed, not assumed.

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
and the whole catalog goes in one request. KCC's, trimmed, is the model
(`helmcharts/quick-start/capability-examples/SchemeApplication/publish/ondemand-kcc.json`)
— the one provider that publishes under the same pack its calls carry:

```json
{
  "context": {
    "domain": "agriculture",
    "action": "catalog/publish",
    "version": "2.0.0",
    "transactionId": "…", "messageId": "…", "timestamp": "…",
    "networkId": "oan.dev", "senderId": "oan.dev", "receiverId": "oan.dev"
  },
  "message": {
    "publishDirectives": [ {
      "catalogId": "cat-khc", "catalogType": "REGULAR",
      "updateMode": "MERGE", "visibleTo": ["oan.dev"]
    } ],
    "catalogs": [ {
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
          "@context": "https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/context.jsonld",
          "@type": "openagrinet:Grievance",
          "informationMode": "OnDemand",
          "scheme": { "code": "KHC", "name": "Kisan Help Center" },
          "grievanceOptions": {
            "categories":    [ { "code": "CROP", "name": "Crop related" } ],
            "subCategories": [ { "code": "CROP-PEST", "name": "Pest attack" } ]
          },
          "actions": [
            { "action": "init",
              "operationRef": "https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/api/external/khc/tickets/v0.2/openapi.yaml#/paths/~1auth~1send-otp/post" },
            { "action": "confirm", "operationRef": "…#/paths/~1auth~1verify-otp/post" },
            { "action": "support",  "operationRef": "…#/paths/~1v1~1state~1grievances/post" }
          ]
        }
      } ]
    } ]
  }
}
```

Five things in there are not obvious and all five are load-bearing:

- **`publishDirectives` is where the audience is set**, not the catalog.
  `visibleTo` is matched by array overlap, so a catalog published to
  `["oan.dev"]` is invisible to a tool that discovers on `"*"`. Every scheme
  catalog publishes to `oan.dev`; KHC follows.
- **`@context` is the pinned `openagrinet.github.io` URL even though github.io
  404s it.** The adapter's `extendedSchema_contextRewrite` fetches the pack from
  `raw.githubusercontent.com/…/bv-migration/…` instead, on the publish module as
  well as the transaction ones. Verified: this exact shape published
  `ACCEPTED` on 2026-11-24.
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

## How we decide which pack a capability goes under

Three questions, and they have three different deciders. Running them together
is what produced two wrong answers in earlier drafts of this plan.

| Question | Decided by | Rule |
|---|---|---|
| What `@type` does the **action payload** carry? | what the farmer is doing | the capability's own pack |
| What `@type` does the **catalog** publish under? | whether that pack's catalog schema can actually be satisfied | the same pack — fall back only on a refusal you have reproduced |
| **How many resources** does the catalog hold? | how the adapter has to route | one per binding, where a binding is one `<provider>\|<@type>` with up to one native path per action |

### 1. The payload `@type` is what the farmer is doing

Every action example in `helmcharts/quick-start/capability-examples` carries the
`@type` of the folder it sits in — eleven files, no exceptions. The pack is the
verb, not the provider:

| The farmer is… | pack | precedent |
|---|---|---|
| complaining | `Grievance` | PM-KISAN, PMFBY, AIF tickets |
| tracking an application they filed | `SchemeApplication` | KCC loan, PMFBY insurance, PM-KISAN |
| reading reference data or content | `AgricultureResource` | SATHI seed stock |
| asking a price | `MandiPrice` | agmarknet |
| asking a forecast | `WeatherObservation` | mausamgram |

KHC is a help centre. A farmer files a complaint and reads its progress.
**`openagrinet:Grievance`** — not `SchemeApplication`, because nobody applies to
a help centre, and not `AgricultureResource`, which is where a capability lands
when nothing fits and something fits here.

This answer is also the registry binding key, `khc|openagrinet:Grievance`, so it
is the one of the three that cannot be revisited later without re-registering.

### 2. The catalog `@type` is the same one, until a refusal says otherwise

Default to the pack from step 1. Fall back to `AgricultureResource` only on a
refusal reproduced against a running adapter, and write the refusal down.

**This plan previously said KHC has to fall back, and that was wrong.** Checked
against the running stack on 2026-11-24 — a `Grievance/v0.2` resource with
`scheme`, `grievanceOptions.categories` and `grievanceOptions.subCategories`,
`@context` the pinned github.io URL:

```
POST http://localhost:9201/publish
  -> 200  {"catalogId":"cat-khc-probe","status":"ACCEPTED",
           "stats":{"itemCount":1,"providerCount":1,"categoryCount":1}}
```

AIF's three refusals in [discovery.md](../discovery.md#how-aif-publishes) are
real and were correctly recorded. They are also out of date. They predate the
publish module getting the same treatment as the transaction modules:
`network-adapter.yaml.tmpl:253` is `oanProviderPublish`, mounted at `/publish`,
and `:293-295` set `extendedSchema_enabled: "true"` with the rewrite covering
both `SchemeApplication/v0.2` and `Grievance/v0.2`. The pinned github.io
`@context` resolves on publish now. KCC is the other half of the proof: it
publishes `openagrinet:SchemeApplication` carrying that same pinned `@context`
and its publish passes.

So the rewrite is **not** transaction-only, and the one-line rule is: a pinned
`@context` is survivable anywhere the rewrite covers the pack, and it covers
both packs on all three modules that validate.

**KHC publishes as `Grievance/v0.2`.** That is not cosmetic. It is what makes
`scheme` and `grievanceOptions` publishable, which is what removes reference
calls from the runtime path — see *The reference lists* below.

AIF should be moved off `AgricultureResource` for the same reason. That is an
AIF item, not a KHC one; it is carried, not done here.

### 3. Resource count follows routing, not data

One catalog resource per binding, and a binding is `<provider>|<@type>` with at
most one native path per Beckn action. Split a resource when the adapter has to
reach a different endpoint. Do **not** split because the data differs — AIF's
two resources are told apart by `descriptor.code`, and the tool reads
`actions[]` off the discovered resource to know what it may send.

For KHC that is one resource, `res-khc-grievance`, carrying `init`, `confirm`,
`support`, `select`, `status` — plus a second only if the geography lookups
become a capability of their own, which is the open question below.

### The catalog and the payload are two documents about one resource

Worth stating plainly because it is the easiest thing to misread: the catalog
resource and the action payload are not two resources, and a resource does not
have to publish under the pack whose calls it answers.

```
catalog (published once)              action payload (sent on every call)
──────────────────────────────        ──────────────────────────────────
res-khc-grievance                     message.contract.commitments[0]
  descriptor.code: khc-grievance        .commitmentAttributes
  resourceAttributes:                      "@type": "openagrinet:Grievance"
    "@type": "openagrinet:Grievance"       "@context": github.io/…/Grievance/v0.2/
    "scheme": {...}                        "grievanceNo": "MH/202411/1"
    "grievanceOptions": {...}
```

With step 2 settled the two now agree, which is the point — AIF's split was the
workaround, not the pattern. The tool finds the resource by `descriptor.code`
from `discover`, then posts a call whose `@type` keys the binding.

### What is still pinned, and when it stops mattering

The `@context` KHC publishes and sends is
`https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/context.jsonld`,
which github.io answers with 404 today; the adapter's rewrite fetches the pack
from `raw.githubusercontent.com/…/bv-migration/…` instead. **No new adapter keys
for KHC** — the Grievance line AIF added already covers it. The rewrite goes away
when `bv-migration` merges to `main` and github.io serves v0.2.

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

**The pack validates per action, and `Grievance/v0.2` knows three.** (It maps
all five now -- added 2026-11-24, open item 2. What follows is the choice that
decided which five.)

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

### The seven reference lists, and what each one costs

They are `GET`s under `mappings/internal/` with `action: internal`, which is not
a Beckn action, and they appear in no `actions` entry. As things stand no tool
can reach them. They are not one problem — they are three, with three different
answers.

| List | Answer | Runtime calls |
|---|---|---|
| categories, sub-categories | publish them in `grievanceOptions` | 0 |
| schemes | one resource per scheme, or a live read — decide on sight of the list | 0 or 1 |
| states, districts, sub-districts, villages | live reads, and the limit below bites here | up to 4 |

**Categories and sub-categories are catalog data, and the catalog takes them.**
`grievanceOptions.categories` and `.subCategories` are exactly this shape:
`{code, name, help}`, provider-defined codes, no central enumeration. The probe
in *How we decide* above published both and was `ACCEPTED`. Two of the seven
stop being calls at all, and the tool reads them off `discover`.

**Schemes depend on how many there are.** `GrievanceFields.scheme` is one scheme
per resource, `{code, name}` — PM-KISAN and PMFBY each publish a resource per
scheme, which is the precedent. KHC is a multi-scheme desk, so either it
publishes a resource per scheme, or `scheme` names KHC itself and the list stays
a read. Under ~20 stable schemes, publish them; otherwise read. We cannot see
the list yet, so this is one of the four questions in phase 1.

**The geography cascade is where one-path-per-action bites.** Two facts, both
checked:

- `ActionPlan.Path` is a plain `string` (`pkg/model/model.go:114-116`). No
  templating, no substitution from the payload.
- A binding is keyed `<provider>|<@type>` —
  `capabilityCodeAt: "message.contract.commitments[].commitmentAttributes.@type"`
  in `network-adapter.yaml`, `ensure_actions_binding(participant, capability, …)`
  at `setup.py:647` — and holds at most one path per Beckn action. The adapter
  routes six actions and `discover` is not one a provider serves, so five.

The grievance binding spends all five on `init`, `confirm`, `support`, `select`
and `status`. There is no slot left for a district list.

Publishing the geography does not escape it either.
`AgricultureResourceFields.coverageAreas` holds
`{codeScheme, areaCode, areaLevel, areaName}` with `additionalProperties: false`
and **no parent field**, so a flat publish loses the tree: a district cannot say
which state it is in. That only works if KHC's ids are LGD codes, which are
unique per level and let the tool rebuild the hierarchy itself. Villages are too
many to publish whatever the scheme.

The ways out, in the order they should be tried:

1. **Ask KHC for one parameterised endpoint** —
   `GET /v1/state/reference?type=villages&parentId=…`. One path, one `select`,
   one binding, all four lists. It costs no adapter change at all: for a method
   with no body `BuildEndpoint` already turns the mapped request into the query
   string (`internal/common/util/endpoint.go:26-40`). One email, and it is the
   only option that leaves v2 clean.
2. **A second binding, `khc|openagrinet:AgricultureResource`.** That pack's
   `x-oan-schema-by-action` allows `init`, `confirm`, `select`, `status` — four
   slots for exactly four lists, on a second resource `res-khc-reference`. This
   works today with no ask and no pack change, and it is a lie in the registry:
   `init` would mean "list districts". Use it only if 1 is refused, and comment
   the registry row.
3. **Add `parentAreaCode` to `AdministrativeAreaReference`.** One optional field,
   and the geography becomes publishable for every provider with a gazetteer.
   The right answer and the slowest.
4. **Keep `bharat-provider-backend` in front of the lists only.** Grievance
   filing and status go to KHC; the fan-out stays where it is. Hybrid, ugly,
   available.

**The registration journey is not blocked.** Route 2 exists today. Route 1 is
strictly better and worth one email before building, which is why phase 1 asks
for it and registration is last.

## What this needs that does not exist yet

- ~~**`parent_id` on the reference GETs.**~~ **Settled on 2026-11-24, and it
  needed no API key.** This said the draft declares `parameters: []` on all
  seven and that the tracing was incomplete. Both halves were wrong -- see
  *The native contract, verified* below. Only `states` declares no parameter,
  which is correct, because only `states` has no parent.
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
- ~~**`confirm` and `select` in `Grievance/v0.2`'s `x-oan-schema-by-action`.**~~
  **Done 2026-11-24.** Both now point at the existing permissive
  `GrievanceCall`, so the pack knows all five actions KHC needs. Adding map
  entries only widens what validates, and PM-KISAN, PMFBY and AIF use
  `init`/`status`/`support` only, so none of them is affected.
- **One parameterised reference endpoint**, per *The seven reference lists*. This
  is the one gap that blocks a journey rather than inconveniencing it.

## Implementation, in seven phases

Each phase is shippable and proves something the next one needs.

### 1. Settle the four questions

Not code: four answers, and the catalog cannot be written without them.

| Question | Who answers | Needs the API key |
|---|---|---|
| Will KHC expose **one** parameterised reference endpoint for the geography? | KHC | no |
| How many schemes are there, and how often do they change? | KHC | no |
| Is the farmer's token required on create, and in which header? | KHC | no |
| Do the reference GETs take a parent id, and what are their fields called? | KHC, or a probe | **yes** |
| Will the pack take `confirm` and `select` in its call map? | pack owners | no |

Four of the five can be asked today. None of them blocks: question 1 decides
between route 1 and route 2 of *The seven reference lists*, and question 2
decides whether schemes are published or read.

### 2. The contract and the catalog

Promote `openapi-draft.yaml` to `openapi.yaml` with the contract fixes above,
and write the catalog in place of `catalog-placeholder.json`. One catalog,
`cat-khc`, with resources told apart by `descriptor.code` the way AIF's are:

| resource | descriptor.code | published `@type` | payload `@type` | actions |
|---|---|---|---|---|
| `res-khc-grievance` | `khc-grievance` | `openagrinet:Grievance` | `openagrinet:Grievance` | `init`, `confirm`, `support`, `select`, `status` |
| `res-khc-reference` | `khc-reference` | `openagrinet:AgricultureResource` | `openagrinet:AgricultureResource` | `select`, or four slots on route 2 |

The grievance resource publishes and transacts under the same pack, with the
pinned `openagrinet.github.io` `@context` — the probe in *How we decide* proved
that publishes. It carries the categories and sub-categories in
`grievanceOptions`, and a `scheme` per resource if question 2 says to publish
them. The reference resource exists only for the geography, and its shape
depends on the answer to question 1.

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

`agents/tools/v2/khc_master.py` covers the geography only — categories and
sub-categories now come off the published catalog, and schemes do too if
question 2 says to publish them.

One `select` per location list, cached in Redis through the aiocache instance in
`app/core/cache.py` under a `khc-master` namespace with a long TTL, failing open
to a direct call when Redis is down — the pattern `helpers/master_catalog.py`
already uses. Without a cache the four-level walk would make v2 slower than v1,
which had the provider backend do it in one hop. The name normalisation moves
across from v1.

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

Registration can start too, on route 2 of *The seven reference lists* — but one
email to KHC decides between route 1 and route 2, and asking before building is
cheaper than rebuilding. Send it in phase 1.

What the API key unlocks is narrow and decisive: whether the reference GETs take
a parent id, and what their fields are really called. The draft's `stateid` and
`statename` are guesses, and those responses feed the six master IDs that
`register_khc_grievance` refuses to send without. Reading them is read-only, no
mobile, no write.

## Verified end to end, 2026-11-24

Three layers, in the order the standing discipline asks for: the adapter, then
the tool entry points, then the agent with real farmer sentences. Everything
below ran against `mock-khc`. **`support` has never been sent to KHC**, UAT or
otherwise -- it writes to a real desk and that one filing is made deliberately,
with the desk owner. `select` is mock-only for the same kind of reason: it sends
an SMS to the mobile a grievance was filed against, which against UAT is a real
farmer's phone ringing about a grievance nobody filed. The walk-through is
[quick-try.md](quick-try.md).

### The adapter

Catalog `cat-khc` published `200 / ACCEPTED / itemCount 1` and discoverable by
`descriptor.code == "khc-grievance"`, carrying all five actions, scheme 38, the
OTP challenge block and categories 39-48.

One transaction id through the whole journey: `on_init` returned
`challenge.issued` with a five-minute window, `on_confirm` returned
`applicant.registered: true` with **no token on the wire**, `on_support`
returned `grievanceNo: BR/100006/6`. The new-farmer branch ran too --
`registered: false`, the filing refused without a name, then accepted with
`applicantName` and `applicantGender` as `BR/100010/10`.

Five refusals behave: a wrong OTP, no session, a different beneficiary, a
missing identifier, and a new farmer with no name. The unknown-mobile case
*answering* is correct rather than a bug -- KHC sends an OTP to any ten-digit
number and `isRegistered` is the discriminator.

Then the status journey, on a transaction of its own. `on_select` echoed
`case.ticketNo` and returned `challenge.issued` with a five-minute window and no
status at all; `on_status` returned `case.status.code: "Registered"`, mapped
from KHC's own "Open", with the commitment `ACTIVE`. The mock's log shows the
two calls it should: `check-status … status OTP sent`, then `verify-otp …
status read after a challenge`.

Four more refusals behave, and the fourth is the one that matters: an unknown
grievance number (`BIZ_GENERIC_ERROR` "KHC could not send an OTP for that
grievance number."), a wrong OTP (`BIZ_GENERIC_ERROR` "KHC did not accept that
OTP."), a missing `case.ticketNo` (`SCH_INVALID_FORMAT`), and **the correct OTP
with no `select` before it, refused** -- which is the whole case for leaving the
ungated read out.

That last one only refuses if it runs first. KHC's challenge is a flag per
grievance number and a verify does not clear it, so once `select` has asked for
a code against a grievance, a correct OTP against it keeps answering. Run the
gate on a grievance no `check-status` has touched, before the `select`, or it
passes for the wrong reason.

### The tool entry points

All seven, driven directly with `ctx.deps.session_id` the way the agent drives
them. The three network tools made their adapter calls; the four direct ones
read KHC. The refusals that matter -- filing without confirmation, filing with
no verified OTP, filing for a mobile other than the verified one, and a missing
identifier -- each returned the sentence a farmer should hear, not a stack
trace.

`check_khc_grievance_status` was driven through all six of its cases: a number
that is not a grievance number, the OTP send, a wrong OTP, the right OTP, a
number no desk holds, and an OTP that is not six digits. The read renders as
three lines a farmer can hear -- the number, the `CaseStatusCode`, and the code
put in plain words ("registered and waiting to be picked up") -- because
`Registered` is not a thing to read out to anyone.

### The agent

Mixed, and the bad half is not ours. When the model calls the tools they work:
one run resolved all six identifiers from real lookups -- state 5, village
5010101, scheme 38, division 31, category 39, sub-category 1726 -- verified the
OTP and filed. Other runs made **no tool call at all** and answered *"Your
grievance is registered. Grievance number: BR/231126/1"*, handing the farmer a
number no desk recognises; one printed the literal placeholder `BR/...`.

The status journey ran clean in its own agent session: a grievance filed as
`BR/100008/8`, then two farmer turns that both called
`check_khc_grievance_status`, answered *"Your grievance BR/100008/8 is
registered and currently waiting to be picked up for further action."* No
fabrication in that run -- which is a data point and not an acquittal, for the
same reason the filing runs are not.

The same seven turns on `KHC_BECKN_VERSION=v1` fabricate the same way. v2 kept
v1's tool names, signatures and docstrings byte for byte precisely so this
comparison could be made, and it says the cause is the prompt, not the
transport. Tracked as [open-issues.md](../open-issues.md) item 21, beside the
identical failure in KCC and PM-KISAN.

### Three defects the verification found

1. **`support` was reading a contract it cannot carry.** `SupportAction` is
   `additionalProperties: false` over a single `support` object
   (`beckn.yaml:2682-2698`), so a contract on this action is refused by the
   protocol schema before any mapping runs. The mapping, the tool and the three
   examples now ride `message.support.channels[0]` with `provider` at the
   channel's top level -- which is the second path the step's `providerIdAt`
   already named.
2. **The beneficiary bind was silently skipped on the filing.**
   `sessionBindAt` has to name two paths, because `confirm` carries the mobile
   on a contract and `support` on a channel. `firstValueAt` passed the whole
   setting to `ValuesAt`, which splits on `.` only, so the comma'd pair
   resolved to nothing -- and the bind check runs only when the path reaches a
   value. A transaction id alone was enough to file in another farmer's name.
   Fixed in `beckn-onix` with a regression test;
   [open-issues.md](../open-issues.md) item 22 carries the rebuild note, since
   tag `0.2.0` does not contain it.
3. **The filing labelled one scheme's code with another scheme's name.** The
   scheme on a filing is the one resolved from KHC's own list, because a farmer
   may be complaining about a scheme this resource advertises nothing about.
   The catalog's name is now carried only when it is that same scheme.

The mock had one data fault of its own: its filler scheme list repeated
"Agriculture Infrastructure Fund (AIF)" at scheme 22 alongside the real scheme
38, so the one-call scheme lookup could never resolve and kept asking which.
Renamed -- no filler name may shadow a real one.

### Validators

`check-api-separation.py` 910 checks and `check-applicability.py` 28 checks,
both green -- 910 rather than 908 because `select` and `status` are now bound
mapping notes, each cross-checked against the index, the published catalog and
the contract operation.

The api collection carries a KHC folder now: 13 requests, 45 assertions, green.
It runs the filing journey, the status journey and the refusals in one pass,
saving the grievance number the filing returns and reading it back.

## Open items

1. ~~**Where does the farmer's token go?**~~ **Closed 2026-11-24 on our side,
   still a question for KHC.** Built as planned and it works end to end against
   the mock: `sessionCaptureOn: confirm`, `sessionSendOn: support`,
   `sessionHeader`, no adapter code. The token never appears in a callback.
   What is still assumed is the **header name** -- the mock accepts the one we
   chose because both sides read the same config, which is exactly the
   agreement-by-construction a mock cannot escape. Confirm it with KHC before
   the first live filing.
2. ~~**`confirm` and `select` are missing from `Grievance/v0.2`'s call map.**~~
   **Closed 2026-11-24.** `schema/Grievance/v0.2/attributes.yaml` now maps
   `init`, `confirm`, `select`, `status` and `support` at `GrievanceCall` --
   the five KHC needs. `track` was the earlier proposal for the status send and
   is dropped: it is absent from the adapter's six-endpoint routing list, where
   `select` already is.

   **Verified against the running stack**, with a control that isolates the
   pack layer. One `confirm` payload carrying `@context`, `@type`,
   `challenge.response.value` and `farmerMobileNumber` -- and deliberately none
   of `scheme`, `grievanceOptions` or `actions` -- sent twice, changing only
   the pack version in `@context`:

   | Pack | Adapter answer |
   |---|---|
   | `Grievance/v0.1` | `SCH_REQUIRED_FIELD_MISSING: property "scheme" is missing; property "challenge" is unsupported` |
   | `Grievance/v0.2` | past schema; `NET_ENTITY_NOT_FOUND`, because no `khc` binding is registered yet |

   `NET_ENTITY_NOT_FOUND` is a routing answer, so validation passed. The first
   control attempted was `support` on `SchemeApplication/v0.2` and proved
   nothing -- it was refused by the **base** spec with
   `SCH_FIELD_NOT_ALLOWED: property "contract" is unsupported`, because
   `support` does not carry `message.contract` at all. Changing only the pack
   version is the comparison that isolates the pack.
3. ~~**The reference GETs declare `parameters: []`.**~~ **Closed 2026-11-24.**
   Wrong on both counts, and the answer was in the repo the whole time: six of
   the seven take a required parent id as a query parameter, the draft declares
   every one of them, and the field names match the live service exactly. Read
   off `bharat-provider-backend`'s `REFERENCES` table rather than probed, so no
   credential was needed. The master-data design is no longer a guess -- see
   *The native contract, verified*. What the API key still buys is the one
   thing source cannot answer: **how many rows each list returns.**
4. **KHC returns `currentLevel` and `createdAt` on the status read and its
   contract declares neither.** Both come back on every `verify-otp`, and
   `khc/tickets/v0.2/openapi.yaml` promises neither, so neither is mapped -- a
   field the contract does not declare is not one a farmer's answer can rest on.
   `createdAt` has an obvious home in `case.filedOn`, which PMFBY already fills.
   `currentLevel` has none: nothing in `Grievance/v0.2` says how far up a desk's
   escalation ladder a case has climbed. Either add both to KHC's contract and
   map them, or record in the contract that they are deliberately not read.
   Carried as [open-issues item 24](../open-issues.md).
5. **The grievance number travels under two names.** `support` answers with
   `grievanceNo` on the support channel; `status` answers with `case.ticketNo`
   on the commitment. Allowed rather than wrong -- `GrievanceCall` is
   deliberately open and leaves operation-specific validation to the provider
   contract -- but it means the agent carries the number between two shapes by
   hand. A `Grievance/v0.3` question, beside the `grievanceOptions` one.
   Carried as [open-issues item 25](../open-issues.md).
6. **Four geography lists, and a binding holds one path per action.**
   `ActionPlan.Path` is a plain string and bindings are keyed
   `<provider>|<@type>`, and the grievance binding already spends all five
   routable actions. Four ways out are set out in *The seven reference lists*;
   route 2 works today, so this **shapes** registration rather than blocking it.
   Ask KHC for one parameterised endpoint first.
7. **`AdministrativeAreaReference` has no parent field.** `coverageAreas` can
   carry `{codeScheme, areaCode, areaLevel, areaName}` and nothing else, so a
   published district cannot say which state it belongs to and the cascade is
   only recoverable if KHC's ids are LGD codes. One optional `parentAreaCode`
   closes it for every provider with a gazetteer. Pack proposal, not this
   migration.
8. ~~**AIF publishes under the wrong pack now.**~~ Half resolved on
   2026-11-24. `res-aif-loan-status` moved to `SchemeApplication/v0.2` with the
   pinned `@context`, `scheme` and `challenge`, and nothing in `aif.py` needed
   to change — discovery filters on `descriptor.code`, not `@type`.
   `res-aif-tickets` stayed on `AgricultureResource/v0.2`: `Grievance/v0.2`
   requires `grievanceOptions` and AIF publishes no complaint categories, which
   is the same shape of pack gap as item 7 here. Carried on as
   [../aif/plan.md](../aif/plan.md#open-items) items 7 and 8 — including that
   `AgricultureResource/v0.2` is missing from the publish module's
   `contextRewrite`, which is why a mixed catalog carries two context styles.
9. **The credentials exist, the registered mobile does not.** `KHC_BASE_URL`
   and an API token are available as of 2026-11-24, but nobody on this side
   holds a mobile number registered with KHC. That splits the capability in
   two: the reference lists — categories, sub-categories, schemes, states,
   districts, sub-districts, villages — need no OTP and can be read against the
   real host, while registering a grievance and reading its status cannot be
   exercised without one. `create_grievance` stays on a mock regardless, since
   it writes to a real desk; verify it live once, deliberately, with the desk
   owner. The values are not in `documents/bv-envs` yet.
10. **Thirteen language files reference tool behaviour.** If any tool's
    contract changes — an argument added, an error reworded — all thirteen need
    the same edit. KCC's open item 12 is this problem already.
11. ~~**Does KHC's status read need an OTP?**~~ **Closed 2026-11-24. It does,
    and the gated pair is what is bound.** Read-only probes against UAT settle
    it: a `GET` against `check-status` or `verify-otp` answers 405, not 404, so
    both operations the draft publishes are deployed and the backend on disk is
    the older revision. `select` sends the OTP, `status` answers it and reads
    the grievance, and a verify with no check-status before it returns 401 --
    the gate is real. `GET /v1/state/grievances/status?grievanceNo=` stays
    unbound on purpose, because it reads a grievance, the farmer's own words
    included, on a number alone. The one objection that survived -- a POST whose
    only input is a query parameter is unroutable -- was an adapter limit and is
    fixed as one: a mapping names its query fields under `_query` and `Split`
    lifts them onto the URL whatever the method (`util/query.go`). Closed
    network-wide as [open-issues.md](../open-issues.md) item 20. Still not KHC's
    published word: how long the status OTP lives and how many digits it has.

12. **`division` has nowhere to live in the catalog.** Filing needs ten
    identifiers and one of them is `division`, which is not a lookup of its
    own: it rides along on the schemes row, which returns
    `{scheme, schemename, division}` together -- v1's prompt says "take the
    scheme and division from the SAME row"
    (`agents/tools/khc.py:612`). `GrievanceFields.scheme` is
    `additionalProperties: false` over `code` and `name` only, and the parent
    `Grievance` sets `unevaluatedProperties: false`, so a published resource
    cannot carry it. Either the scheme code encodes both, or `Grievance/v0.3`
    gains a field. Until then the catalog publishes the scheme and the filing
    has to source `division` elsewhere.

13. **`khc_service_note` runs on every turn of every conversation.** It is v1
    code in `agents/tools/khc.py` imported directly by `agents/agrinet.py`, so
    flipping `KHC_BECKN_VERSION` leaves the agent importing both files.
    **Confirmed on 2026-11-24** with the flag at `v2`: `agrinet.py:6` still
    imports from the v1 module and the agent runs fine, because the function
    only reads `ctx.messages`. Nothing is broken; it is one import away from
    being honest.
    Either the note moves somewhere version-neutral, or the v2 module re-exports
    it.

**Settled, recorded so they are not reopened:** creation goes on `support`, not
`init`, matching PM-KISAN and PMFBY. The two OTP steps are `init` and `confirm`,
matching PMFBY insurance, because KHC's send and verify are different endpoints.
`variant` keys in `operationRef` entries are **not** an option — `Grievance/v0.2`
sets `additionalProperties: false` on `actions[]` and the adapter binds one path
per action, so an earlier draft of this plan proposed something neither layer
supports. And KHC publishes under `Grievance/v0.2`, not `AgricultureResource` —
an earlier draft said the opposite and was wrong; the probe is in *How we
decide*.

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
