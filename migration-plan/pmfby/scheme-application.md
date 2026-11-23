# PMFBY SchemeApplication: policy and claim status on v0.2

**Status: planned, not built.** This covers the two PMFBY tools `plan.md` said
could not move — `initiate_pmfby_status_check` and
`check_pmfby_status_with_otp`. That document was right at the time: the
blocker was `SchemeApplication/v0.1`, which defines PM-KISAN schemas and
nothing else. v0.2 removes it, because `SchemeApplicationFields` takes any
provider's `scheme.code` and `SchemeApplicationCall` validates only that a
payload claims the pack. **We go to v0.2.**

## The flow today, and after

Today the BAP is the integration. `pmfby_scheme_status.py` holds the PMFBY
base URL, logs in for a token, posts the OTP, posts the verify, then calls
the policy or claim API directly and masks the response on the way out.

After, the tool holds one URL — the adapter — and asks the network who
serves this:

```
  discover ──▶ adapter ──▶ discovery service
           ◀── provider + resource + the actions it advertises

  init     ──▶ adapter ──▶ PMFBY getOtp
  confirm  ──▶ adapter ──▶ PMFBY verifyMobile        session captured
  status   ──▶ adapter ──▶ PMFBY farmerpolicylist    session spent
  select   ──▶ adapter ──▶ PMFBY claimSearchReport   session spent
```

| | v1 | v2 |
|---|---|---|
| provider URL | in the tool | in the registry |
| token login | in the tool | adapter, `authScheme: tokenHeader` |
| OTP session | tool variables | adapter cache, bound to the mobile |
| PII | fetched, then masked | never fetched |
| who the tool knows | PMFBY | the adapter |

`discover` goes to the adapter too — `routing-network-adapter-consumer.yaml`
sends it to the network module and everything else to the provider module.
One base URL, `OAN_ADAPTER_URL`, nothing else configured.

**Discover first, then the transaction calls.** Each tool discovers once at
the top, and both of its action calls reuse that one result — the provider
and the resource id come off the wire and go straight into the envelope.
Discover is not a formality: the tool also checks that the published resource
advertises the action about to be sent, the `_offers` check in
`agents/tools/v2/aif.py`, and refuses rather than sending an action the
catalog never claimed.

The filter matches `@type` **and** `scheme.code`, as `_discover_application`
does (`agents/tools/v2/pmkisan_scheme_status.py:183`). `@type` alone would
return every scheme publishing `SchemeApplication`.

## What the catalog publishes, and why

The rule: publish what the next call needs the farmer or the agent to
**choose** — but only from fields the pack already defines. **The pack does
not get widened for one provider.** If a value has nowhere to go in the
schema, it does not get published; it goes in the mapping.

The whole publishable surface is six fields. The pack's own README lists
them, and `SchemeApplicationFields` closes the list
(`unevaluatedProperties: false` on the container):

| field | required | |
|---|---|---|
| `@context`, `@type` | yes | pack identity |
| `informationMode` | yes | `OnDemand` |
| `scheme` | yes | code and name |
| `actions` | yes | advertised operations |
| `challenge` | optional | public verification methods |

`profile.json` says why it is this short: *"Discovery contains public service
metadata only."* Note that `context.jsonld` is wider than this — it also
defines `applicant.mobile`, `enrolmentId`, `challenge.response` and the rest.
Those are **payload** terms for the transaction calls; only the six above may
appear in a published catalog.

| field | what the next call gets from it |
|---|---|
| `scheme.code: PMFBY` | the discover filter; `@type` alone returns every scheme on this pack |
| `challenge.methods[0]` | OTP, SMS, length 6, NUMERIC — the tool checks the farmer's OTP against this instead of a hardcoded 6 |
| `actions[].action` + `operationRef` | the `_offers` check, and which verb carries policy versus claim |
| `actions[].description` | so `policy_status -> status` and `claim_status -> select` is read, not assumed |

Two fields the current `res-pmfby-insurance` publishes have to go:
`subjectCategories` is an AgricultureResource field that the SchemeApplication
container would reject, and `@type` moves with it.

### The season codes are not published — for now

They have no home in those six fields, and giving them one would mean adding
to a pack PM-KISAN and AIF also publish, to carry a value only PMFBY needs.
**TODO, deliberately deferred:** if a later release adds an options field to
`SchemeApplicationFields` — the way `Grievance/v0.2` carries
`grievanceOptions` — move the season list into the catalog and have the tool
read it, as `sathi_seed.py` reads crops off the discovered resource
(`:262, :363`). Until then the translation lives in the mapping:

| | who does it |
|---|---|
| the farmer says "Kharif 2026" | the tool passes the words through |
| `Kharif` + `2026` -> `sssyID: "04010026"` | `insurance.status.yaml` |
| `Kharif` + `2026` -> `season=1&year=2026` | `insurance.select.yaml` |

This is what mapping files are for — absorbing one provider's odd field
shapes so nothing upstream of the adapter learns them. It also puts the three
season names in exactly one place, which is worth something: today
Kharif/Rabi/**Zaid** is hardcoded in `agents/tools/v2/pmfby_grievance.py:69`
while PMFBY's own contract says **Summer** (`openapi.yaml:126`). Two
hardcoded lists that already disagree; after this, one mapping owns the
names and the tool owns none.

The OTP length is the opposite case and shows where the line falls: the pack
already has `challenge.length`, so it is published and the tool reads it
rather than hardcoding 6.

## The constraint that shapes the bindings

A registry binding holds **one path per action name**. Not a convention —
`toProviderRecord` builds `map[string]model.ActionPlan` keyed by action
(`beckn-onix/.../sunbirdRegistry/providerrecord.go:333`), so a second entry
for the same action overwrites the first. The path is joined literally:
`BuildEndpoint` concatenates base URL and `call.Path`, no templating
(`beckn-onix/.../internal/common/util/endpoint.go:16`).

**Three of the four operations sit on `action: status`.** The registry row is
where that actually bites, and the registry is written from the `PROVIDERS`
table in `setup.py`, not from the contract. `x-oan-binding` is read by
nothing — the spec's own index says so: *"it is not part of the HTTP
request"* (`api/external/index.md:9`). It still has to be corrected, because
it is what a reader takes as the intent, and it currently documents a shape
that cannot be built.

| line | operationId | binding today | binding needed |
|---|---|---|---|
| 38 | `sendPmfbyInsuranceOtp` | `AgricultureResource` / `init` | `SchemeApplication` / `init` |
| 16 | `verifyPmfbyInsuranceOtp` | `AgricultureResource` / `status` | `SchemeApplication` / `confirm` |
| 60 | `listPmfbyInsurancePolicies` | `AgricultureResource` / `status` | `SchemeApplication` / `status` |
| 109 | `listPmfbyInsuranceClaims` | `AgricultureResource` / `status` | `SchemeApplication` / `select` |

Three paths, one slot; which survived would depend on registry row order.
`api/external/pmfby/catalog.json` repeats the same labels, and it *is*
machine-read — the tool's discover filter and its `_offers` check run against
it.

The same four labels live in three places, with different weight:

| | machine-read | by |
|---|---|---|
| `openapi.yaml` `x-oan-binding` | no | people — the contract of record |
| `setup.py` → registry `ProviderSchema.actions` | yes | the adapter, on every call |
| published catalog `actions[]` | yes | the tool, before it sends |

They must agree, but only the last two can break a call.

`resourceType` in the contract moves to `openagrinet:SchemeApplication` for
the same documentary reason — AIF's loan contract already says it.

The catalog's `@type` is a separate decision, and both patterns exist today:
AIF publishes its resources as `AgricultureResource` and discovers them by
`descriptor.code` (`aif.py:231`); PM-KISAN publishes `SchemeApplication` and
filters on `@type` plus the scheme code
(`pmkisan_scheme_status.py:197`). PMFBY follows PM-KISAN, because the pack
whose actions it is publishing is SchemeApplication.

A correction I owe from earlier: I described the four mapping files as work
already done. They are `status: draft-native, deployable: false`, 13 to 16
lines each — sketches of upstream field names, not adapter mappings. A real
one is `config/mappings/pmfby/grievance.status.yaml`, 132 lines.

`confirm` and `select` are the two new verbs. `confirm` for verify is the
pattern `ffs|openagrinet:FarmerSession` already uses. `select` for claims is
the weakest of the four — not wrong, it is a filtered read over claim
records, but chosen because `status` is taken. The alternative is a second
capability pack for claims: new `@type`, new pack, second catalog resource,
second binding, to serve one GET. If a reviewer disagrees, this is the line
to argue with.

AIF avoided all of this because one AIF URL serves both halves of its OTP
exchange.

## What the tool looks like

Same shape as the other v2 tools — transport, input, output, exported tools —
and the same two tool names and arguments v1 exports:

```
initiate_pmfby_status_check(ctx, phone_number)
check_pmfby_status_with_otp(ctx, otp, phone_number, inquiry_type, year, season)
```

The agent prompt is untouched and the farmer sees the same two steps. Mandi
is the precedent for a v2 migration that changes nothing visible.

`check_pmfby_status_with_otp` makes two adapter calls: `confirm` to spend the
OTP and learn `farmerId`, then `status` or `select` on `inquiry_type`. **This
is the one place the plan departs from AIF, deliberately.** AIF splits verify
into its own tool, which works because AIF's session is held adapter-side and
verify returns nothing the next call needs. PMFBY's reads need `farmerId` in
the payload — see below — so splitting the tools would route a farmer's
provider id through the LLM and back. Keeping both calls inside one function
is both the simpler code and the safer one.

### What this does not carry over from v1

| v1 | v2 |
|---|---|
| `PII_CODES` and `mask_pii_value` (`pmfby_scheme_status.py:38-91`) | gone — the mapping never sends the fields, so there is nothing to mask |
| full request and response payload logging, phone included (`:514-524, :616-626`) | action and status code only |
| `generate_transaction_id` = `uuid5(session_id + phone)` | a fresh `uuid4` per journey |
| base URL, token login, header assembly | the adapter's |

Nothing is imported from `agents/tools/pmfby_scheme_status.py`. Masking a
response you should not have fetched is a v1 workaround; the allow-list
replaces it.

## Security

The threat worth taking seriously is **one farmer reading another farmer's
policies and claims.**

### Why `farmerId` travels in the payload

`farmerID` is a required query parameter on both reads
(`openapi.yaml:167-168`), and the adapter cannot put a session value in a
query string — a mapping sees exactly two roots, `beckn` and `_local`
(`internal/common/serve.go:297`), and a captured session only ever leaves as
a header (`session.go:180`). So it travels in the payload, and the gate is
the session rather than secrecy. Three controls, all already in the adapter:

1. **No session, no call.** `sessionSendOn: "status,select"` makes the
   adapter fetch the session *before it builds the request*
   (`serve.go:214-220`), and `sessionCredentialFor` returns a 400 —
   *"this request needs a verified session and there is none, or it has
   expired"*. A caller supplying somebody else's `farmerId` on a fresh
   transaction id never reaches PMFBY at all.
2. **The session is pinned to the verified mobile.** `sessionBindAt` stores a
   value from the confirm *request* and compares it on every call that spends
   the token (`session.go:291-297`): *"this request is for a different
   beneficiary than the verified session"*. Bind on `applicant.mobile` — the term the pack's
   context defines and the path PM-KISAN already sends
   (`pmkisan_scheme_status.py:254-270`) — and **not** on `farmerId` — the bind is read from the request at capture time, and at
   confirm time the tool does not yet know the `farmerId`. The mobile is also
   the right property: the session belongs to the number that got the OTP.
3. **The session expires.** `sessionTtl: 15m` with `sessionTtlMargin`. An OTP
   cannot be spent twice — `keepSession` fails the request rather than
   answering if the token cannot be stored (`session.go:341-347`).

Control 2 has a condition worth writing down: the check is skipped when
`held.Bind` is empty (`session.go:291`). The confirm payload **must** carry
the mobile at the configured path, and status and select must carry it too,
or the check silently does nothing. A test that confirms as one mobile and
reads as another belongs in step 7.

### What the mappings must not return

The spec says it plainly: *"Accounts and land details may also be returned;
they remain private"* (`openapi.yaml:207`). **The draft mapping is
`jsonata: response` — the whole upstream body.** That is bank accounts and
land records into a signed Beckn answer. Both reads allow-list instead:

| policies | claims |
|---|---|
| `policyID`, `insuranceCompanyName` | `applicationNo` |
| `applicationNo`, `cropName` | `ClaimStatus` / `Status` |
| `policyStatus`, `seasonName`, `seasonYear` | `ClaimType`, `claimDate` |
| `sumInsured`, `claimAmount`, `claimStatus` | `amount`, `totalPayable` |

Nothing else crosses, including whatever a future PMFBY release adds. Same
discipline `grievance.status.yaml` already applies.

### What never travels

- **The OTP** goes into the confirm request and no further.
- **`farmerId` is never in the tool's answer text.** It exists to make the
  next call; the agent has no use for it and the transcript must not carry it.
- **Provider credentials stay with the adapter.** The two headers, `token`
  and `password` (`openapi.yaml:158-164`), come from the environment through
  `pmfby.yaml.tmpl`. No mapping and no payload names either.
- **PMFBY's own wording is logged, not returned.** The mappings put it in
  `_error.cause`, which `refusalIn` keeps for the log and never sends to the
  caller (`serve.go:318`). The adapter already declines to log the provider
  body — *"it can carry the farmer's details"* (`serve.go:268`).

## Three blockers, stated up front

### 1. Nothing documented returns `farmerID`

`OtpReply.data` is *"boolean true or an object with verified true"*
(`openapi.yaml:180`) — no farmer id. The draft mapping's note says "bind
session.farmerID from verified lookup result.farmerID", pointing at a
*lookup* call that is not in the spec.

Load-bearing twice: it is the query parameter both reads need, and the
candidate for `sessionTokenField`, the field the adapter stores as the
session. If verifyMobile answers with nothing storable, `keepSession` keeps
nothing and every read is refused for a reason that looks like a bug.

**I cannot settle this without the real API,** and I will not invent a field
name. What gets built: the confirm mapping reads the id from the documented
`data` object — `lookupField` matches segments case-insensitively
(`session.go:360-372`), so casing is tolerated — and emits an explicit
`_error` when it is absent, so the first real call says what is missing
instead of sending `farmerID=undefined`. The field name stays a one-line edit.

### 2. The session header collides with PMFBY's provider token

`pmfby.yaml.tmpl:16` puts the provider's login token in `Authorization`, and
a captured session also leaves as `Authorization: Bearer ...` by default
(`session.go:180-186`). On a read, both want the same header.

AIF already hit this and solved it: its provider credential sits in `apiKey`
(`aif.yaml.tmpl:9-10`) so the bearer is free for the farmer — *"apiKey says
which service is calling, the bearer says which farmer"*.

The insurance contract points the same way. Its two schemes are `token` for
the provider credential and `password` for the OTP (`openapi.yaml:158-164`);
neither is `Authorization`. So insurance wants `headerName: token`, and the
session can keep the default. Grievance keeps `Authorization` — it is a
different host and a different contract.

What this needs: confirming that the insurance host reads `token` rather than
`Authorization`, which is the same phone call as blocker 3.

### 3. No base URL

The registry holds `https://pmfby.unreachable.invalid`. Grievance is
`/krphapi/...`, insurance is `/api/v1/...`, almost certainly a different
host, so it needs `PMFBY_INSURANCE_BASE_URL` carried on the capability row
the way PM-KISAN carries `PMKISAN_CHATBOT_BASE_URL` (`bin/setup.py:166-167`).

Grievance has been blocked on the same missing values since 2026-11-20
(`plan.md`). This plan does not unblock that.

## What gets written

### network-specs

| file | change |
|---|---|
| `schema/SchemeApplication/v0.2/attributes.yaml` | add `confirm` and `select` to `x-oan-schema-by-action` |
| `api/external/pmfby/insurance/v0.2/openapi.yaml` | the four `x-oan-binding` blocks, per the table above |
| `api/external/pmfby/insurance/v0.2/mappings/` | replace the four sketches with real mappings, flat (`init`, `confirm`, `status`, `select`), as grievance has them |
| `api/external/pmfby/catalog.json` | `res-pmfby-insurance`: four distinct actions, `@type` `openagrinet:SchemeApplication` |

The pack edit is two lines and purely additive — all four actions point at
the same permissive `SchemeApplicationCall`, and `Grievance/v0.2` already
lists three actions this way. No new fields: the pack is shared with PM-KISAN
and AIF and does not get widened for one provider. Nothing that validates
today stops validating.

Not a change, a reminder: `resourceAttributes.@context` stays on the
`openagrinet.github.io` address. It is the pack's identity, stamped into
every catalog and payload. The adapter rewrites the *fetch* address through
`extendedSchema_contextRewrite`; pointing the published context at a branch
URL would leave dead links in stored catalogs the day the branch is deleted.

### helmcharts

| file | change |
|---|---|
| `config/mappings/pmfby/insurance.init.yaml` | new — mobile + `otpType: SMS` to getOtp, issue the challenge |
| `config/mappings/pmfby/insurance.confirm.yaml` | new — mobile + OTP to verifyMobile, return `farmerId`, refuse if absent |
| `config/mappings/pmfby/insurance.status.yaml` | new — `farmerID`, `listType`, `sssyID` sent; mobile required but not sent; fields allow-listed |
| `config/mappings/pmfby/insurance.select.yaml` | new — `farmerID`, `searchType`, `season`, `year` sent; mobile required but not sent; fields allow-listed |
| `config/adapters/providers/pmfby.yaml.tmpl` | the `"__PARTICIPANT_ID__\|openagrinet:SchemeApplication"` session block |
| `bin/setup.py` | the capability row under `pmfby`; `PMFBY_INSURANCE_BASE_URL` in `needs` |
| `capability-examples/SchemeApplication/publish/ondemand-pmfby.json` | new — the catalog to publish |
| `.env.example` | the new variable, as a placeholder |

Each mapping is written once and copied to both repos, which is how the
grievance pair already lives — the network-specs and helmcharts copies of
`grievance.status.yaml` are byte-identical today.

**No new provider step.** `config/adapters/steps/Grievance.yaml.tmpl` already
serves SchemeApplication bindings, and says why: a provider's two bindings
refer to each other, and a binding can only name another inside its own step.
PMFBY insurance joins that step's `bindingKeys` on its own: the step id is
read from the provider's fragment, not the capability (`setup.py:750`), and
`__BINDING_KEY__` is the comma-joined list of that provider's bindings
(`setup.py:734`). Adding the capability row is the whole edit.

The session block, modelled on `aif.yaml.tmpl`:

```
"__PARTICIPANT_ID__|openagrinet:SchemeApplication":
  sessionKeyAt: "context.transactionId,context.transaction_id"
  sessionCaptureOn: confirm
  sessionSendOn: "status,select"
  sessionTokenField: <what verifyMobile returns>       # blocker 1
  sessionTtl: 15m          # a guess; PMFBY does not document OTP lifetime
  sessionTtlMargin: 60s
  sessionBindAt: "message.contract.commitments[].commitmentAttributes.applicant.mobile"
```

`sessionCaptureOn` and `sessionSendOn` are comma-split lists
(`session.go:92`), so `confirm` and a two-action send need no adapter change.

### Season and year

The two reads want it differently, and the mappings own the difference — the
tool passes the farmer's words through:

| | policies | claims |
|---|---|---|
| season | inside `sssyID` | `season`, as `1`/`2`/`3` |
| year | last two digits, inside `sssyID` | full four digits |

`sssyID` is `"040" + seasonCode + "00" + lastTwoOfYear` — Kharif 2026 is
`04010026` (`openapi.yaml:72`). Codes are 1 Kharif, 2 Rabi, 3 Summer. PMFBY
says **Summer** where the `Grievance` pack enum says **Zaid**; the v2
grievance tool already carries that mismatch and this one will too.

### bharat-oan-api

| file | change |
|---|---|
| `agents/tools/v2/pmfby_scheme_status.py` | new — OTP length read off the discovered resource; no season codes, the mapping owns those |
| `agents/tools/__init__.py` | route the two tools through `_beckn_v2("PMFBY")` |
| `migration-plan/pmfby/plan.md` | link here; correct the "cannot move" rows |
| `migration-plan/README.md` | add the row |

`PMFBY_BECKN_VERSION=v2` already exists and already switches the two
grievance tools, so insurance rides the same flag. Deliberate: both
capabilities take an OTP from the same provider, and a farmer who verified on
one version must not be able to spend it on the other.

## Order of work

1. The pack edit. Two lines; everything else depends on it.
2. The four bindings in `openapi.yaml` and `catalog.json`.
3. The four mappings, in both repos, with the allow-lists.
4. `pmfby.yaml.tmpl`, `setup.py`, `.env.example`.
5. The publish JSON, and publish it.
6. The tool, and the `__init__.py` switch.
7. Verify.

### What step 7 can and cannot prove

| check | provable now? |
|---|---|
| catalog publishes, discover returns `res-pmfby-insurance` | yes |
| the resource advertises all four actions | yes |
| the tool carries no hardcoded provider or resource id | yes — `disc_e2e.py` already asserts this for the other three |
| the tool carries no hardcoded OTP length | yes — it comes off the discovered resource |
| status before confirm is refused | yes — the adapter refuses on its own cache, no provider needed |
| confirm as one mobile, status as another, is refused | **no** — capturing a session needs confirm to succeed, and confirm calls PMFBY |
| the four upstream calls work | **no** — `pmfby.unreachable.invalid` |
| the allow-lists drop what they should | **no** — needs a real response body |

Steps 1 to 6 land and are reviewable. Step 7 stops where the provider does.

**On a mock:** it would close blocker 2 and make blocker 1 *look* closed,
because the mock would return `farmerID` wherever this plan guessed — every
test would then prove the mapping agrees with the mock. The two session
refusals above are worth more, because they are the adapter's own behaviour
and hold whoever answers. Note also that mocks are pulled as published images
rather than built by the stack (`docker-compose.yml:520`), so `mockpmfby` is
a Go service to write, build and publish.

## Open questions for review

1. `select` for claims — accept, or spend a second pack on it?
2. Bind the session on the applicant's mobile — agreed? It is the only value
   present in the confirm request, and `farmerId` cannot be used.
3. Is there a PMFBY contact who can confirm three things — what
   `verifyMobile` returns, which header the insurance host reads, and the
   insurance base URL? All three blockers are that one call.
