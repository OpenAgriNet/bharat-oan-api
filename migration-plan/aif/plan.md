# AIF — Beckn v2 migration plan

AIF (Agriculture Infrastructure Fund) answers four questions today: two are an
OTP handshake, two are a status read. This plan moves all four to v2 on the
`v0.2` packs.

Two principles shaped it:

- **The pack describes the catalog; the provider's OpenAPI describes the call.**
  That is what `v0.2` means, and SATHI already runs that way.
- **Reuse what is in the adapter.** Everything AIF needs exists except one
  thing — a place to hold a verified session. The Redis `cache` plugin is that
  place; nothing new gets written to store it.

Status: **not started.** Everything below is settled from source, not from a
conversation with the provider.

## The four calls

| v1 tool (`agents/tools/aif.py`) | v2 action | Capability | Native call |
|---|---|---|---|
| `initiate_aif_otp` | `init` (start) | `openagrinet:SchemeApplication` v0.2 | `POST /validate_beneficiaries/` `{beneficiaryId}` |
| `verify_aif_otp` | `init` (verify) | `openagrinet:SchemeApplication` v0.2 | `POST /validate_beneficiaries/` `{beneficiaryId, otp}` |
| `check_aif_loan_status` | `status` | `openagrinet:SchemeApplication` v0.2 | `GET /Status` `?loanApplicationNumber` |
| `check_aif_grievance_status` | `status` | `openagrinet:Grievance` v0.2 | `GET /support-tickets` `?beneficiaryId` |

There is no grievance **filing** call anywhere in AIF. The app sends a new
grievance to KHC instead (`assets/prompts/skills/aif/status/status_en.md`).
AIF's `Grievance` capability publishes `status` and nothing else.

Both status calls need the session the `init` pair created — including the
`Grievance` one, on the other binding. PM-KISAN already crosses capabilities
this way with `otpBindingKey` (`pmkisan.yaml.tmpl:24`).

`transaction_id` is `generate_transaction_id(session_id, beneficiary_id)` —
deterministic, so all four calls share one key. That is what lets one OTP cover
a whole conversation.

## What the provider actually does

Read out of `bharat-provider-backend` (the v1 BPP) on `dev`. These replace the
open questions an earlier draft carried.

| Question | Answer, and where it comes from |
|---|---|
| Is the OTP single-use? | **Yes.** `status_en.md`: "An OTP is used once. Never send an old OTP to any tool again." The BPP caches the token per `transaction_id` (`aif-session.store.ts`) precisely so a second OTP is never needed. |
| Can we re-present the OTP per call, like PM-KISAN? | **No.** That is the one model AIF rules out. A server-side session is mandatory. |
| Does `GET /Status` need a JSON body? | **No.** The BPP sends body *and* query only because "some proxies drop bodies on GET — AIF ignores the spare copy" (`aif.service.ts`). Query alone is right, and `util.BuildEndpoint` already turns a mapped request into a query string for bodyless methods. |
| How does auth work? | **Two credentials at once:** a service key in an `apiKey` header from `AIF_API_KEY`, **and** `Authorization: Bearer <token>` once a session exists. Base `AIF_BASE_URL`, timeout `AIF_TIMEOUT` (default 20000). |
| How long does a session last? | `ExpiresIn` from the verify response, default 3600s, minus a 60s safety margin. |
| What do failures look like? | Non-2xx: `{Message, ErrorCode, ExceptionMessage}` — only `Message` is fit for a farmer. Inside a 200: `Success !== true`. Loan: a non-string body, or a string matching `/not found/i`. Tickets: a non-array object carrying `Message`. |
| Field-name traps? | **Yes.** Most responses use `Success`/`Message`; the `BV42201` error uses `success`/`message`. The BPP reads every field case-insensitively and so must we. |
| What does `/Status` return on success? | A **bare JSON string**, e.g. `"Disbursed"` — not an object. |
| What does `/support-tickets` return with no tickets? | A string, not an empty array. `LoanApplicationNumber: 0` means the ticket is not tied to an application. |
| Anything dead in `aif.py`? | `_failure()` reads `values.get("retryable")`. The BPP never emits it. Do not port it. |

`AIF_BASE_URL`, `AIF_API_KEY` and `AIF_TIMEOUT` are in none of our repos yet.

## What already exists, and what AIF reuses

This is the heart of the plan. Each row was checked in the code.

| AIF needs | Reused from | New code? |
|---|---|---|
| Session storage with TTL | `definition.Cache` — the Redis `cache` plugin. `Get`/`Set(key, value, ttl)`/`Delete` is exactly the shape needed. | No |
| Capture the token off the `init` response | The `common.Envelope` wrapper pattern. `Seal()` returns an `Opener` that sees the raw answer — `Grievance/followup.go` is the precedent for wrapping a call this way. | A wrapper, no new mechanism |
| Read `transaction_id` for the key | `contextValue(becknContext, "transaction_id", "transactionId")`, `serve.go:486` | No |
| Send `apiKey` as a header | `authScheme: header` + `headerName` + `headerValueEnv` | No |
| Send the session as a bearer | `authenticate()`, `auth.go:379` | ~10 lines |
| GET with a query string | `util.BuildEndpoint` — for bodyless methods the mapped request becomes the query automatically | No |
| A bare-string response body | `decodeBody` unmarshals into `any` (`serve.go:350`) | No |
| Case-insensitive field reads | JSONata in the mapping. PM-KISAN already falls through `Rsponce`/`Responce`/`Status`/`status`. | No |
| Non-2xx bodies reaching the mapping | `AnswerStatuses` (`auth.go:55`) | No |
| Two `init` calls on one action name | Both share path and method, so one `ActionPlan` serves them; the mapping branches on whether `otp` is present. | No |

**Rejected after checking:** `PayloadStore`. It stores Beckn payloads indexed by
message and transaction for replay protection — there is no "put this arbitrary
value" method, and a session token is not a payload. Use `Cache` directly.

### The two gaps, and why they are small

**1. Provider steps are the only plugin kind not handed a Cache.**

| Constructor | Gets a `Cache`? |
|---|---|
| `Manager.Registry`, `Manager.DeDiRegistry` | yes |
| `Manager.PayloadStore`, `Manager.ManifestLoader` | yes |
| `Manager.ProviderStep` (`manager.go:725`) | **no** |

Closing it follows a pattern already used four times:

- `definition/step.go:43` — add `cache Cache` to `ProviderStepProvider.New`
- `plugin/manager.go:725` — pass it through
- `handler/stdHandler.go:678` — hand over the cache it already holds
- six `*/cmd/plugin.go` files — accept the parameter; five ignore it
- `common.New` — keep it on the `Step`

**2. One binding supports one auth scheme.** `authenticate()` is a
`switch cfg.Scheme` (`auth.go:381`), so `apiKey` and `Authorization` cannot both
be expressed as schemes. They do not need to be: the session is not an auth
scheme, it is a per-transaction layer. Keep `authScheme: header` for the apiKey
and set the bearer after the switch, when a session exists for this
transaction. No new scheme, no change to any existing provider.

### The session itself

Config on the binding, parsed by the existing `ParseStepConfig` path — the
`Grievance` step needs no change, because `ParseEnvelopes` already hands it
everything it does not recognise (`Grievance/cmd/plugin.go:28`).

```
sessionKeyAt:       context.transactionId,context.transaction_id
sessionCaptureOn:   init
sessionSendOn:      status
sessionTokenField:  Token
sessionTtlField:    ExpiresIn
sessionTtl:         1h
sessionTtlMargin:   60s
sessionBindAt:      message.contract.commitments[].commitmentAttributes.beneficiaryId
sessionBindingKey:  <participant>|openagrinet:SchemeApplication   # Grievance side only
```

Behaviour: on capture, `Set("aif:session:<transactionId>", {token, beneficiaryId},
ExpiresIn-60s)`. On send, `Get`; miss or expiry is a clean "session expired"
answer, not a 500; a beneficiary that does not match the stored one is refused.
The token is stored and sent and **never** appears in a Beckn response — the
BPP is explicit about this and so are we.

Redis gives expiry for free and removes the multi-replica problem the BPP has:
its own comment says to move off in-memory before scaling out.

## Does AIF on v0.2 force PM-KISAN and PMFBY off v0.1?

**No.** They stay where they are.

The extended validator resolves a pack from the **`@context` URL carried in the
payload** (`schemav2validator/extended_schema.go:440`), not from the registry. A
PM-KISAN payload naming `Grievance/v0.1/context.jsonld` and an AIF payload
naming `Grievance/v0.2/context.jsonld` are each validated against their own
pack, in one adapter, at the same time.

The only collision is bookkeeping, and it does not have a one-line fix.
`SchemaRegistry.json` declares `uniqueIndexFields: ["capabilityCode"]`, so the
registry cannot hold two rows for one code no matter what filter
`ensure_row` uses — a deployment advertises exactly one pack version per
capability. Seeding AIF at v0.2 therefore rewrote PM-KISAN's row, which is why
`ensure_capability_schema` now prints the conflict and **leaves the existing
row alone**. Nothing in the adapter reads it (`schemaUrl` has no consumer
there), but `oanfed.py:schema_packs()` publishes exactly that set, so the
deployment advertises SchemeApplication and Grievance at v0.1 while AIF speaks
v0.2. Resolving it means moving every provider of a code to one version.

Moving PM-KISAN and PMFBY to v0.2 later is not a version bump. v0.1 validates
call payloads through `x-beckn-container-by-action` (`init`/`status` →
`commitmentAttributes`, `support` → `channels`); v0.2 validates the catalog
only and has one container, `resourceAttributes`. The move touches seven
mapping files, both helm templates' `providerIdAt`/`capabilityCodeAt`,
`Grievance/otp.go:70` `challengeIn()` — which reads the v0.1 containers
directly — and the tool-side payload builders, and then has to be re-proven
against a real PM-KISAN host we have never reached and a PMFBY base URL we do
not have. Do it as its own piece of work, after AIF ships.

## OpenAPI-driven execution

The intent is that the call comes from the provider's contract, not from a path
typed into a registry row. SATHI shows the design already in place: the
catalog's `resourceAttributes.actions[].operationRef` points into
`api/external/sathi/stock/v0.2/openapi.yaml`, and that file carries
`x-oan-binding` and `x-oan-mapping` on the operation itself.

**The adapter does not read it.** `grep -r operationRef beckn-onix` returns
nothing; method and path come from the registry row. The two have already
drifted on SATHI — the OpenAPI says `/inv-apis/stock/getSeedAvailability`,
`setup.py:142` seeds `/inv-apis2/...`.

Resolving `operationRef` adapter-side is a platform change, not an AIF change:
it alters how every binding is registered and needs a fallback so mandi,
weather, SATHI, PM-KISAN and PMFBY keep working. It is larger than all of AIF's
own code.

**So: ship AIF with the path in the registry row, as all five existing
providers do.** AIF's catalog and mappings still carry `operationRef`, so the
design is correct and in place; when resolution lands, nothing in AIF changes —
it just starts being read. Tracked as open item 3.

## What gets built

### network-specs

1. Promote `api/external/aif/{loan,tickets}/v0.2/openapi-draft.yaml` to
   `openapi.yaml`, with real `servers` once `AIF_BASE_URL` is known.
2. Add `x-oan-binding` and `x-oan-mapping` to each operation, as SATHI's does.
   Today the drafts carry the binding under `x-draft` only.
3. Collapse the two `init` entries. The loan draft lists `init` twice (variants
   `AifStart`, `AifVerify`) against the same
   `#/paths/~1validate_beneficiaries~1/post`. One operation, one mapping,
   branching on `otp`. Merge the two files under `loan/v0.2/mappings/init/`.
4. Move the four mappings off `status: source-traced, deployable: false` and
   drop the `binding: Design only` note once the adapter work lands.
5. Replace `catalog-placeholder.json` with a real catalog: one provider, two
   resources (`aif-loan` → `SchemeApplication`, `aif-tickets` → `Grievance`),
   `@context` at `v0.2`, `actions[].operationRef` into the published OpenAPI.

### beckn-onix

Only what the two gaps above require.

| File | Change |
|---|---|
| `definition/step.go` | `cache Cache` on `ProviderStepProvider.New` |
| `plugin/manager.go` | pass it through |
| `handler/stdHandler.go` | hand over the cache it holds |
| 6 × `*/cmd/plugin.go` | accept it; 5 ignore it |
| `internal/common/common.go` | hold the `Cache`; parse `session*` keys |
| `internal/common/auth.go` | set the bearer after the scheme switch |
| `internal/common` session wrapper | capture on `init`, send on `status`, in the `Envelope`/`Opener` pattern |
| `Grievance/*` | **none** |

### helmcharts

- `config/adapters/providers/aif.yaml.tmpl` — two bindings on the shared
  `Grievance` step, `authScheme: header` for the apiKey, plus the `session*`
  settings and `sessionBindingKey` on the Grievance side.
- `config/mappings/aif/{scheme-application.init,scheme-application.status,grievance.status}.yaml`.
- `setup.py` — an `aif` entry with two capabilities at `v0.2`; the `version`
  fix to `ensure_capability_schema`.
- `.env.example` — `AIF_BASE_URL`, `AIF_API_KEY`, `AIF_TIMEOUT`,
  `AIF_PARTICIPANT_ID`, `AIF_MAPPING_BASE_URL`, `AIF_BECKN_VERSION`.
- Confirm the adapter deployment has the Redis `cache` plugin enabled; the
  session depends on it.

### bharat-oan-api — built

- `agents/tools/v2/aif.py` — four tools, same names and signatures as
  `agents/tools/aif.py`, behind `AIF_BECKN_VERSION` (`tools/__init__.py:19`),
  defaulting to `v1`. All four move on one flag because they share one OTP and
  one session.
- Payloads go in `commitmentAttributes` — not `resourceAttributes`, as this
  plan said before the shape was settled — with the v0.2 `@context`.
- `transaction_id` is `generate_transaction_id(session_id, beneficiary_id)`,
  the same derivation v1 used. It is also exactly what the adapter keys the
  session on, so the OTP bought in step 2 is the one steps 3 and 4 spend, and
  a question about a different beneficiary lands on a different session.
- `OAN_SCHEMA_BASE` overrides the pack host, defaulting to the published one.
  v0.2 is not published yet; the override is how the tool reaches a locally
  served pack. Delete it the day v0.2 goes public.
- No `_loggable()` equivalent, because there is nothing to mask: the v2 tool
  logs the action, the URL and the status code and never the payload.
- `retryable` is gone. The adapter classifies the refusal — `BIZ_*` is AIF's
  own answer and is passed to the farmer verbatim.
- `assets/prompts/skills/aif/status/status_en.md` does not change: one
  verification per conversation, reuse the beneficiary ID, never resend an OTP.
  The v2 path honours it rather than rewriting it.

## Order of work

1. Obtain `AIF_BASE_URL` and `AIF_API_KEY`. Nothing is verifiable without them.
2. beckn-onix: plumb the `Cache` into provider steps, then the session wrapper
   and the bearer. Tests at each step.
3. network-specs: promote the OpenAPIs, collapse `init`, write the catalog.
4. helmcharts: provider template, mappings, `setup.py`, env.
5. bharat-oan-api: the v2 tools, flag off. **Done.**
6. Run all four calls against the real AIF host. Blocked on credentials;
   everything up to here is verified against `mock-aif` instead.
7. Flip `AIF_BECKN_VERSION=v2`. Then `quick-try.md` and the README row.

Steps 2 and 3 are independent and can run together.

## Open items

1. **`AIF_BASE_URL`, `AIF_API_KEY`, `AIF_TIMEOUT` are in none of our repos.**
   No longer blocking — the flow is verified against `mock-aif` — but nothing
   is confirmed against AIF itself until they arrive. Ask whoever runs the v1
   BPP. Swapping them in is a `.env` edit and one `make register-provider
   PROVIDER=aif UPDATE=1`; see `quick-try.md`.
5. **The v0.2 packs are not published at their canonical URLs.** Production
   Pages still builds from `main`, which carries v0.1 only, so every
   `openagrinet.github.io/network-specs/schema/*/v0.2/` URL 404s. Since
   2026-11-24 a full branch preview is served at
   `https://openagrinet.github.io/network-specs/previews/bv-migration/`, where
   all three v0.2 packs answer 200. That is a preview path, not the URL the
   packs pin, so the adapter's `extendedSchema_contextRewrite` still has to
   redirect `Grievance/v0.2` and `SchemeApplication/v0.2` to the raw branch
   URLs. Merging `bv-migration` into `main` is what retires the rewrite.
2. **Redis has to be on for the adapter.** The session is not optional and
   there is no in-memory fallback by design — a fallback would work on one
   replica and silently lose sessions on two.
3. **`operationRef` resolution** is deferred, as argued above. When it lands,
   fix SATHI's published path at the same time or the first provider to use it
   calls the wrong URL.
4. **PM-KISAN and PMFBY to v0.2** is its own piece of work, scoped above.
6. ~~**AIF is not in the catalog.**~~ Resolved on 2026-11-23, and the pack it
   publishes under was corrected on 2026-11-24. Both capabilities are one
   catalog with two resources told apart by `descriptor.code`; see
   [../discovery.md](../discovery.md#how-aif-publishes). The catalog lives at
   `helmcharts/quick-start/capability-examples/Grievance/publish/ondemand-aif.json`
   and in the API collection under **AIF - loan and grievance status**.

   It first published both resources as `AgricultureResource/v0.2` because the
   fallback was written when `SchemeApplication/v0.2` was thought to be
   unpublishable. It is not: the publish module resolves a pinned `@context`
   too, so `res-aif-loan-status` now publishes as `SchemeApplication/v0.2` with
   the pinned github.io context, `scheme`, and a `challenge` naming its
   six-digit SMS OTP — the shape KCC and PMFBY already use. AIF's own loan
   draft asked for exactly that (`resourceType: openagrinet:SchemeApplication`
   in `x-draft.catalogCandidate`). Nothing in `aif.py` changed: `_discover`
   filters on `descriptor.code`, never on `@type`.

7. ~~**`res-aif-tickets` cannot move to `Grievance/v0.2`.**~~ Resolved on
   2026-11-25 by fixing the pack rather than the resource. The blocker was that
   `GrievanceFields` required `grievanceOptions` unconditionally, so publishing
   AIF's tickets was refused with `SCH_REQUIRED_FIELD_MISSING: property
   "grievanceOptions" is missing`. AIF publishes no complaint-category list
   anywhere and has no filing call at all — `subQueryType` arrives only inside
   a ticket it has already created — so inventing codes to satisfy the schema
   would have put fiction in a catalog.

   `Grievance/v0.2/attributes.yaml` now requires `grievanceOptions` only of a
   resource whose `actions` advertises `support`, which is what asking for
   categories is actually about: they are what a caller needs in order to file.
   All three existing grievance resources advertise `support`, so none of them
   validates any more loosely than before. `res-aif-tickets` publishes as
   `Grievance/v0.2` with `informationMode`, `scheme`, and the one `status`
   action, and the capability is reachable: it was answering
   `NET_ENTITY_NOT_FOUND` before, because the adapter is bound on
   `aif|openagrinet:Grievance` and the tool was sending
   `AgricultureResource` straight from the catalog.

   The contract agreed all along — `operationId: AifTickets`, a description
   reading `grievance_status`, `api/external/README.md` listing the pack as
   Grievance, `aif.yaml.tmpl` carrying a `openagrinet:Grievance` binding, and
   `aif.py` already using `GRIEVANCE_TYPE`. Only `catalog.json` and the
   operation's `x-oan-binding.resourceType` said otherwise. `aif.py` is
   unchanged: `pack_attributes` echoes the published `@type`, so correcting the
   catalog was enough.

8. **`AgricultureResource/v0.2` is missing from the adapter's
   `extendedSchema_contextRewrite`.** Only `SchemeApplication/v0.2` and
   `Grievance/v0.2` are listed, so an `AgricultureResource` resource pinning
   the canonical context is refused with `SCH_SCHEMA_ADAPTATION_FAILED: HTTP
   404 fetching .../schema/AgricultureResource/v0.2/attributes.yaml`. That is
   why SATHI's catalog still carries raw `refs/heads/bv-migration` URLs while
   KCC, PMFBY and both AIF resources carry pinned ones. AIF's tickets left this
   set with item 7: `Grievance/v0.2` is rewritten, so moving the resource onto
   that pack let it pin the canonical context too. Add the third entry in
   `network-adapter.yaml.tmpl` so all three packs read the same, or merge to
   `main` and delete the rewrite entirely.

   Still open underneath all of this: the `operationRef`s name `openapi.yaml`
   while only `openapi-draft.yaml` exists, so those URLs 404. The v0.2 pattern
   forbids the `-draft` name, and `aif.py` never fetches them, so the journey
   works. Promoting both AIF drafts the way KCC's was — rename to
   `openapi.yaml`, add `x-oan-binding` per operation, replace
   `catalog-placeholder.json` with `catalog.json` and `index.md` — is what
   closes it.

## Verified, and what it took

Re-verified on 2026-11-24 after the loan resource moved to
`SchemeApplication/v0.2`. Publish returned `ACCEPTED` with two items; both
`discover` calls return their resource with the new `@type` and the pinned
context; all four tool entry points answer correctly, including a refused OTP;
and the agent runs the whole journey with farmer sentences. One agent run
failed first and it is not a regression: given *"check my AIF loan status, my
beneficiary id is 101"* the model skipped `initiate_aif_otp` and asked for an
OTP that was never sent, so the mock refused it. That is the same
tool-skipping bug as [open-issues.md](../open-issues.md) item 13, it lives in
the skill prompt, and agent-prompt work is parked.

All four calls run end to end through the adapter against `mock-aif`, plus
every refusal path: unknown beneficiary, wrong OTP, replayed OTP, a loan
number AIF does not hold, a beneficiary with no tickets, a status call with no
session, and a status call asking about a different beneficiary than the one
the session was bought for. The token is never in a response.

The same twelve paths then ran again through `agents/tools/v2/aif.py` rather
than by hand, and the farmer-facing wording came out right: the OTP prompt,
`Loan application 201: Disbursed`, tickets grouped under
`Application 201 — 1 ticket(s)`, and `There are no open grievances or support
tickets.` for a beneficiary who has none. `AIF_BECKN_VERSION=v2` binds all
four names to `agents.tools.v2.aif`; unset, they still bind to
`agents.tools.aif`.

One behaviour differs from the by-hand runs. A status call naming a different
beneficiary than the verified one is refused as "no session" rather than as a
binding mismatch, because the tool derives the transaction id from the
beneficiary id — a different beneficiary never reaches the verified session in
the first place. Both refusals are correct and both tell the farmer to verify
again.

The whole journey then ran through the agent itself — moderation, the real
model picking its own tools, three turns of history — not by calling the four
functions. The model called `initiate_aif_otp`, asked for the OTP, then
`verify_aif_otp` and `check_aif_loan_status`, and answered a follow-up about
tickets with `check_aif_grievance_status` on the session already bought. With
`parallel_tool_calls` on it sometimes fires a status call in the same batch as
the first `initiate_aif_otp`; that call is refused for want of a session and
the farmer sees only the OTP prompt. The ordering rule is in the skill prompt,
not in the tools, and it is the same on v1.

That run found one gap. The skill prompt tells the agent to read the masked
mobile back to the farmer, and v1's tool returns a `Registered mobile:` line
for it — the v0.2 `init` mapping was dropping AIF's `MobileNumber`, so v2 had
nothing to read. The mapping now carries it as `challenge.issued.sentTo` and
the tool prints the same line v1 does. AIF masks the number itself, so no full
mobile travels.

The eleven requests in the collection's **AIF - loan and grievance status**
folder cover the same ground for anyone without a Python shell: three registry
reads, the four-call journey on one `transactionId`, and four refusals.

Two things had to change that the plan above did not anticipate.

**The v0.2 packs rejected every operational payload.** The base spec's
`Attributes` schema requires both `@context` and `@type` and declares
`@context` as `type: string`, so an attributes container is *always* resolved
to a pack and validated, and the packs' array-form `@context` branch is
unreachable. `SchemeApplication/v0.2` and `Grievance/v0.2` are catalog-only —
`unevaluatedProperties: false`, `required` naming `actions`, no
`x-oan-schema-by-action` — so an `init` or `status` payload carrying
`beneficiaryId` could never pass. The earlier idea of carrying `@type` without
`@context` so validation skips the object is dead for the same reason.

The fix is in network-specs: `x-oan-schema-by-action` on both v0.2 types
points `init`/`status`/`support` at a new permissive `SchemeApplicationCall` /
`GrievanceCall` (`required: ['@context','@type']`, `additionalProperties:
true`), leaving `catalog`/`publish` on the strict type. That is v0.2's own
stated division of labour — the pack describes the catalog, the API contract
the `actions[].operationRef` names describes the call.

**A JSONata function body is a single expression.** `tickets.status.yaml`
declared a local inside `function($v) { ... }` and the mapping failed to
compile at request time with `S0202: expected "}", got ";"`. The body needs a
parenthesised block. The mapper logs the compile failure and then calls the
provider anyway before failing the step, so the symptom is a
`NET_INTERNAL_ERROR` on a request that did reach AIF.

## Risks

1. **The session is the whole flow.** Wrong TTL, wrong key, or a missing
   beneficiary binding means a farmer is asked for a second OTP AIF will not
   accept — or, worse, one farmer's token answers another's question. The
   beneficiary-bound-to-transaction check is not optional.
2. **Case-sensitivity.** A mapping reading only `Success` passes every happy
   path and fails on the one error AIF spells `success`. Read both, everywhere,
   from the start.
3. **Changing `ProviderStepProvider.New` touches six plugins.** Mechanical, but
   all six must be rebuilt together — a stale `.so` fails to load against the
   new interface.
4. **Everything below is proven against a mock, not against AIF.** The real
   service needs an OTP to a mobile number nobody on this side holds, so a
   mock was authorised for this capability alone. The shapes it answers with
   come from the provider document, not from an observed response: the bare
   JSON string on `/Status`, the `Success`/`success` spelling split and the
   `ExpiresIn` field are all still unconfirmed against the real host.
