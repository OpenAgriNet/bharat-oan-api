# KCC — Beckn v2 migration plan

KCC (Kisan Credit Card, served by the Kisan Rin Portal) answers one question
today: what is the status of my credit-card application. Three model tools ask
it. This plan moves all three to v2 on the `SchemeApplication/v0.2` pack.

Two principles shaped it, the same two that shaped AIF:

- **The pack describes the catalog; the provider's OpenAPI describes the call.**
- **Reuse what is in the adapter.** Four things KCC needs are not there. All
  four are additions beside existing code, not changes to it, so no provider
  running today moves.

Status: **built and verified end to end against `mockkcc`, 2026-11-24.** The
adapter path (publish → discover → init → status, plus every refusal) passes
45/45 assertions in the `KCC - loan application status` folder of the API
collection, and all three v2 tools were run against the same stack. What is
below was read out of the code, the parity sheet's `APIs` tab (row 32) and the
existing draft in network-specs — not from a conversation with the provider.

**Decision, 2026-11-24: we have no KCC contact.** So the whole thing is built
and proven against a `mockkcc`, exactly as PMFBY and AIF were, and nothing is
designed around a question only KCC can answer. One fact stays unverified until
credentials arrive — the AES key and its IV rule — and it is listed as the only
real blocker at the end.

## The three calls

| v1 tool (`agents/tools/kcc.py`) | v2 action | Capability | Native call |
|---|---|---|---|
| `initiate_kcc_otp` | `init` | `openagrinet:SchemeApplication` v0.2 | `POST /auth/requestOtp` |
| `check_kcc_application_status` | `status` | `openagrinet:SchemeApplication` v0.2 | `POST /bharatVistaar/krishika/applicationStatus` |
| `select_kcc_application` | **none** | — | no upstream call; re-renders what `status` already returned |

A fourth call exists and is never published: `POST /auth/token`, the adapter's
own login. It is in the OpenAPI so the contract is complete, and out of
`actions[]` so nobody can ask the network for it.

Two things make KCC different from every capability migrated so far.

**There is no `confirm`.** KCC checks the OTP and returns the applications in
the same call. The shape is `init → status`, not AIF's `init → init(verify) →
status` or PMFBY's `init → confirm → status`. The OTP travels on the guarded
call itself, as `commitmentAttributes.otp` — KCC's own field name, read off the
operation the catalog's status `operationRef` points at.

**`select` makes no call.** v1 re-posts `status` with an `application_no` tag
and no OTP, which only works because the v1 BPP cached the list. Our OTP is
single-use, so a genuine second call would be refused. The v2 tool holds the
list against the transaction id — the same in-process cache
`v2/pmfby_scheme_status.py` already uses for `farmerId`. So there is no `select`
binding, no `select` mapping and no `select` in the catalog.

`transaction_id` is `generate_transaction_id(session_id, mobile_number)` —
deterministic, so both calls share one key. That is what the adapter keys the
session on, and what makes a question about a different mobile land on a
different session rather than on this farmer's.

## What happens when a farmer asks

In plain English, with no Beckn words. The farmer says *"what happened to my
KCC application?"*

| Step | What happens | What the farmer sees |
|---|---|---|
| 1 | The tool asks the network who serves KCC. | nothing |
| 2 | The tool asks for the farmer's mobile number. | *"What is your registered mobile number?"* |
| 3 | The adapter logs in to KCC with our own username and password. This has nothing to do with the farmer. | nothing |
| 4 | The adapter asks KCC to send an OTP to that number. | nothing yet |
| 5 | KCC sends the SMS and gives us back a ticket number (`requestID`). We hold it for 5 minutes. | an SMS arrives |
| 6 | | *"I have sent a code to 98XXXXXX10. It is valid for 5 minutes."* |
| 7 | The farmer types the 6 digits. | — |
| 8 | The tool checks it is 6 digits before doing anything. If not, it asks again — no call is made, so nothing is wasted. | *"That should be 6 digits, please check."* |
| 9 | The adapter sends the ticket number and the code to KCC together. | nothing |
| 10 | KCC checks the code and, in the same reply, returns the applications. | — |
| 11 | The tool remembers the list and reads it out. | *"You have 2 applications. KCC2024001 — sanctioned, ₹2,50,000 at SBI Pune. KCC2024002 — still under review."* |
| 12 | The farmer asks about one of them. The tool answers from what it already has. **No call to KCC.** | *"KCC2024001 was sanctioned on 14 Jan for ₹2,50,000 of the ₹3,00,000 you asked for."* |

Four things worth knowing about this flow:

- **The code works once.** KCC spends it on step 10. Asking about a second
  application does not need a new one, because step 11 kept the list.
- **The code dies in 5 minutes.** After that the farmer starts again at step 4.
  We expire our own ticket at the same time, so the farmer is never left
  holding a code that cannot work.
- **A different number means a different conversation.** If the farmer gives
  one number at step 2 and another at step 9, the call is refused before it
  reaches KCC. Otherwise a conversation could be used to read someone else's
  application.
- **A rejected application does not say "rejected".** KCC puts it back to
  `DRAFT`. The only thing that gives it away is a rejection reason being
  present. The tool already knows this and says "rejected" anyway.

## What the provider actually does

| Question | Answer, and where it comes from |
|---|---|
| Base URL | `https://fasalrin.gov.in/kccintegration` — **production**, not UAT. Parity sheet row 32, column B. |
| How does the adapter authenticate? | `POST /auth/token`, **form-urlencoded**, `{entityType, userID, password}` → `{status: true, data: {token, sessionTTL: 3600}}`. The token goes in a header literally named `token` (the draft's `securitySchemes.service`). |
| How does the farmer authenticate? | An OTP to the registered mobile, answered on the status call. |
| What is `requestID`? | Issued by `requestOtp`, required by `applicationStatus`. It is **not** a credential — it exists before the OTP is answered, so holding it reads nothing. The OTP is the secret and KCC checks it. |
| How are the bodies encrypted? | AES-256-CBC over UTF-8 JSON, ciphertext as **hex**, sent as `{"data": "<hex>"}`. Both request and response. `openapi-draft.yaml` says so on both operations. |
| How long does the OTP last? | `expiresIn: "300"` — five minutes, as a string. |
| What does success look like? | `{applications: [{applicationNo, applicationCurrentStatus, requiredLoanAmount, sanctionedAmount, bankName, branchName, rejectionReason, updatedAt, applicationStatusHistory: [{createdAt, applicationStatus}]}]}` |
| Shape traps? | **Yes.** The draft notes the decrypted `data` can be one application, an array of them, or `{applications: [...]}`. The mapping must read all three. |
| A domain trap? | **Yes, and it is the one worth keeping.** A rejected application is put back to `DRAFT`, so the bare status hides the rejection — `rejectionReason` is what tells them apart. `kcc.py:187` already encodes this. |
| What do failures look like? | **Unknown** — the sheet shows success bodies only, and we have no contact to ask. Gap 2 makes the plan work either way. |

`KCC_BASE_URL`, `KCC_TOKEN_URL`, `KCC_ENTITY_TYPE`, `KCC_USER_ID`,
`KCC_PASSWORD` and the encryption key are in none of our repos.

## Schema — nothing changes

| Side | Schema | Change? |
|---|---|---|
| Catalog, `resourceAttributes` | `SchemeApplication/v0.2`, strict | **No.** `informationMode`, `scheme`, `challenge.methods` and `actions[]` is exactly what KCC publishes |
| Transaction, `commitmentAttributes` | `SchemeApplicationCall` (`attributes.yaml:27`) | **No.** It requires `@context` and `@type` and leaves everything else to the provider contract |

That second row is why this is cheap. v0.2's stated division of labour — the
pack describes the catalog, the API contract describes the call — means PMFBY
can return `policies`, AIF can return `application` and KCC can return
`applications`, all on one pack, none of them touching it.

`challenge.methods` is a catalog fact and is already in the context and the
vocab; it is where the tool reads that the OTP is six numeric digits. The
*call* carries KCC's own field names instead, because the API contract
describes the call. Nothing to add to the pack either way.

## Plugin — reuse `Grievance`

**No new Go package.** `openagrinet:SchemeApplication` is served by the existing
`Grievance` step, which is twenty lines wrapping `common.New`. AIF, PMFBY and
PM-KISAN all run through it. KCC is a fourth provider on the same step.

**PoCRA is not a model for this.** `AgricultureFacility` is a stateless search —
a point and a category in, facilities out, `authScheme: none`, no session, no
envelope. It shares nothing with KCC beyond being a provider step. The right
precedents are **AIF** for the session and **PM-KISAN** for the envelope.

### Reused as-is

Each row was checked in the code.

| KCC needs | Reused from | New code? |
|---|---|---|
| A service token cached to its lifetime | `authScheme: tokenHeader`, `token.go:100` | No |
| That token in a header named `token` | `headerName` — PMFBY already uses it for `Authorization` | No |
| Encrypted request and response bodies | the `envelope` framework: `Seal` returns an `Opener`, `serve.go:230` | No — one new codec, below |
| Decryption **before** the session is captured | already the order: `open()` at `serve.go:241`, `keepSession` at `serve.go:254` | No |
| `requestID` held against the transaction | the `session*` settings, `session.go:29` | No |
| An OTP lifetime stated as the string `"300"` | `asSeconds`, `session.go:403` — `ParseFloat`, so a quoted number is fine | No |
| Refusing a request for a different mobile | `sessionBindAt`, `session.go:292` | No |
| Non-2xx bodies reaching the mapping | `AnswerStatuses`, `auth.go:61` | No |
| One application, an array, or `{applications:[…]}` | JSONata in the mapping | No |

### The four gaps

**1. A third codec.**

| Kind | Cipher | Key | Wire |
|---|---|---|---|
| `aesGcm` | AES-GCM | fixed, from env | base64 |
| `aesCbcKeyInBand` | AES-128-CBC | fresh per request, sent as `cipher@key` | base64 |
| **new `aesCbcFixedKey`** | AES-**256**-CBC | fixed, from env | **hex** |

New file `internal/common/envelope/cbcfixed.go`, beside `gcm.go`, same
`seal`/`Open` interface. `checkToken` already allows neither `envelopeTokenEnv`
nor `envelopeTokenField` (`envelope.go:314`), which is what KCC wants — its
token is a header, not a body field.

One shared helper has to grow: `decodeSealed` (`envelope.go:368`) is
base64-only and both existing codecs call it. It takes an encoding, or the new
codec brings its own decode. Either way it is a few lines, and base64 stays the
default so neither existing provider changes.

**2. A failure reply with nothing to decrypt.**

A success looks like `{"status": true, "data": "<hex>"}`. A wrong OTP almost
certainly looks like `{"status": false, "message": "Invalid OTP"}` — no `data`
at all. `sealedIn` (`envelope.go:342`) looks for the sealed string, does not
find it, and returns a 502 `NET_DOWNSTREAM_UNAVAILABLE`. **The mapping is never
run**, so the farmer is told KCC is down when all they did was mistype.

| Add | Meaning |
|---|---|
| `envelopeResponseOptional: true` | when the response carries no envelope, hand the raw body to the mapping instead of refusing |

| Reply | Flag off (today) | Flag on |
|---|---|---|
| `{"status":true,"data":"<hex>"}` | decrypt → mapping | decrypt → mapping, unchanged |
| `{"status":false,"message":"Invalid OTP"}` | 502 "KCC unavailable" | raw body → mapping |

The mapping then decides, instead of the codec deciding for it:

```jsonata
$exists(response.applications)
  ? { "applications": [ ... ] }
  : { "error": { "code": "BIZ_KCC_REFUSED", "message": response.message } }
```

Defaults to `false`, so PM-KISAN and SATHI do not move. It costs about fifteen
lines. **It is worth it because it works whichever shape KCC's errors turn out
to be** — if KCC does encrypt its errors, the flag simply never fires, and we
never had to ask. The price is that the mapping must carry an explicit "I do
not recognise this" branch, or a garbled reply gets rendered as a business
failure. That is one line of JSONata and the mapping should have it anyway.

**3. A form-encoded login with three fields.**

KCC's `/auth/token` is `application/x-www-form-urlencoded` with `entityType`,
`userID` and `password`. `exchangeQueryToken` (`token.go:111`) posts **JSON**
with exactly **two** fields.

| Add | Meaning |
|---|---|
| `tokenEncoding: form` | post form instead of JSON. The transport already takes a content type — `postForToken` is shared with `oauth2`, which already sends form |
| `tokenExtraFields` | one or more constant fields beside the user and the secret |

Both default to today's behaviour, so no existing provider moves.

**4. `tokenExpiryField` cannot read KCC's lifetime.**

KCC answers `sessionTTL: 3600` — a **relative** duration. `expiryLifetime`
(`token.go:262`) reads an **absolute** expiry: a number is `time.Unix(n, 0)`,
a string must be RFC 3339. `time.Unix(3600, 0)` is 1970, so the lifetime comes
out negative and the exchange fails permanently with "carries no future
data.sessionTTL".

Ship with the static fallback — `tokenTtl: 1h`, from which `TokenRefreshSkew`
already takes 60s (`constant.go:103`). `tokenTtl` and `tokenExpiryField` are
mutually exclusive by design (`auth.go:163`), so this is the supported way to
say it. A `tokenTtlField` for relative seconds would be about five lines reusing
`asSeconds`, and is worth adding later; it is not worth blocking on, because the
comment on `tokenTtl` is right that a configured copy of the issuer's number
goes stale silently, and 1h matches what KCC states today.

**Rejected after checking:** putting `requestID` into the encrypted body from
the session layer. `sessionCredentialFor` returns a `sessionCredential{Name,
Value}` and `s.call` sets it as a **header** (`session.go:299`). There is no
path from the cache into the request body, and adding one would be a new
mechanism. PMFBY already solved this — see below.

### How `requestID` reaches the body

The same way PMFBY's `farmerID` reaches a query string, and for the same
reason: the adapter cannot put a held value into a request it has already
mapped.

1. `init` answers `on_init` carrying `requestID` in `commitmentAttributes`,
   beside the masked mobile. In parallel the session layer holds it against the
   transaction id, bound to the mobile.
2. The tool sends it back on `status`.
3. The `status` mapping reads it out of `commitmentAttributes` and puts it in
   the plaintext body, which the codec then seals.
4. The session layer, independently, refuses the call if no session is held for
   this transaction or if the mobile does not match the one the session was
   bought for.

So the session is a **gate**, not a transport — exactly what
`insurance.confirm.yaml` says about `farmerID`. Returning `requestID` to the
caller is safe for the same reason returning `farmerID` is: holding it reads
nothing, because the OTP is the secret and the gate runs first.

### How the OTP travels

The OTP never goes near the session layer. It is farmer input on one call, and
it is spent on that call.

| Hop | Carries the OTP? | Shape |
|---|---|---|
| KCC → farmer's phone | — | SMS, sent by KCC when `init` lands |
| farmer → tool | yes | the six digits typed into the chat |
| tool → adapter (`status`) | yes | `commitmentAttributes.otp = 123456`, the field name and integer type both read off KCC's contract |
| adapter mapping → plaintext body | yes | `otp`, **as a number**, beside `requestID` |
| codec → KCC | yes | inside the AES-256-CBC hex blob, so it is never in clear on the wire |
| adapter → tool (`on_status`) | **no** | the answer carries applications, never the OTP |

Three guards sit on it, each already written:

- The tool refuses anything that is not exactly six digits before it makes a
  call — `kcc.py:347` does this on v1 and v2 keeps it. A typo costs nothing.
- `_loggable` masks the OTP to `****` before anything is logged. Kept as-is.
- The session gate runs before the call goes out, so an OTP sent against a
  transaction with no live `requestID`, or against a different mobile, is
  refused without reaching KCC.

What the OTP is **not**: it is not cached, not held in the session, and not
re-sent by `select`. v1's `select_kcc_application` re-posts `status` with no
OTP at all, which only works because the v1 BPP kept the list. v2 keeps the
list tool-side instead, so `select` makes no call and needs no OTP.

The OTP is named `otp` on the call because that is what KCC's contract calls
it, and the mapping passes it through rather than translating a Beckn name into
a provider one. `challenge` stays in the pack for the catalog side: the issued
block on `on_init` and `challenge.methods` on the offer, which is where the
tool learns the OTP is six numeric digits. Nothing in the pack changes.

### The binding

`helmcharts/quick-start/config/adapters/providers/kcc.yaml.tmpl`:

```yaml
__PROVIDER_CONFIG__ Grievance
              # The deployment's own login, not the farmer's. Form-encoded
              # with a third constant field, which is why tokenEncoding and
              # tokenExtraFields are here and nowhere else.
              #
              # tokenTtl rather than tokenExpiryField: KCC states sessionTTL as
              # a relative 3600, and tokenExpiryField reads an absolute expiry.
              __PARTICIPANT_ID__:
                authScheme: tokenHeader
                tokenUrl: "__KCC_TOKEN_URL__"
                tokenEncoding: form
                tokenExtraFields: "entityType=$KCC_ENTITY_TYPE"
                tokenUserField: userID
                tokenUserEnv: KCC_USER_ID
                tokenSecretField: password
                tokenSecretEnv: KCC_PASSWORD
                tokenResponseField: data.token
                tokenTtl: 1h
                headerName: token
              "__PARTICIPANT_ID__|openagrinet:SchemeApplication":
                envelope: aesCbcFixedKey
                envelopeKeyEnv: KCC_ENCRYPTION_KEY
                envelopeRequestField: data
                envelopeResponsePaths: "data"
                # A wrong OTP almost certainly comes back with no data field.
                # Without this the codec turns that into "KCC is unavailable"
                # before the mapping can turn it into "check the code".
                envelopeResponseOptional: true
                # requestID is issued before the OTP is answered, so it is not
                # a credential on its own. It is bound to the mobile anyway, or
                # a transaction id becomes a key to another farmer's flow.
                sessionKeyAt: "context.transactionId,context.transaction_id"
                sessionCaptureOn: init
                sessionSendOn: status
                sessionTokenField: requestID
                sessionTtlField: expiresIn
                # The session dies with the OTP it belongs to. A requestID that
                # outlives its OTP is of no use to anyone.
                sessionTtl: 5m
                sessionTtlMargin: 30s
                sessionBindAt: "message.contract.commitments[].commitmentAttributes.mobileNumber"
                sessionHeader: X-KCC-Request-Session
                sessionPrefix: ""
```

`sessionHeader` is set for the same reason PMFBY sets one: the default is the
Bearer convention on `Authorization`, which here already carries nothing and
would be misleading. KCC ignores the header; the value that matters travels in
the sealed body.

## Catalog and discovery

One catalog, one resource, modelled on
`capability-examples/SchemeApplication/publish/ondemand-pmfby.json`.

| Field | Value |
|---|---|
| catalog id | `cat-kcc-loan` |
| provider id | `kcc` |
| resource id | `res:kcc:loan` |
| descriptor code | `kcc-loan` |
| `@context` | `…/schema/SchemeApplication/v0.2/context.jsonld` |
| `challenge.methods` | `OTP / SMS / 6 / NUMERIC` |
| `actions[]` | `init`, `status`, each with `operationRef` into the published `openapi.yaml` and `attributesAt: $.message.contract.commitments[0].commitmentAttributes` |
| `visibleTo` | `["oan.dev", "net-b.oan.dev"]` |

`visibleTo` matters and is easy to get wrong: discovery matches it by plain
array overlap, not by wildcard. A catalog published to `{*}` is found only by a
discover whose `networkId` is literally `"*"`. PMFBY, PM-KISAN and the rest
publish to `oan.dev`, so KCC does too, and the tool's discover names the same
network.

The tool reads the provider and resource ids off the discover answer. Nothing
about KCC is hard-coded in the tool — the rule set for mandi, weather and SATHI.

## OpenAPI-driven execution

`api/external/kcc/loan/v0.2/` already holds `openapi-draft.yaml` and three
mappings, all `status: source-traced`, `deployable: false`,
`binding: Design only`. They are shape notes traced from the v1 BPP, not
working JSONata.

| Step | What |
|---|---|
| 1 | `openapi-draft.yaml` → `openapi.yaml`, with a real `servers` entry |
| 2 | `x-oan-binding` and `x-oan-mapping` on each operation, as SATHI's has. Today the binding sits under `x-draft` only |
| 3 | Keep `/auth/token` in the file, out of `actions[]` |
| 4 | Write the two real mappings; delete `mappings/internal/token.yaml`, because the login is `authScheme` config and not a mapped call |
| 5 | Drop `deployable: false` and the `binding: Design only` note when the adapter work lands |

**The adapter does not resolve `operationRef`** — method and path come from the
registry row. **The tool does.** `agents/tools/v2/provider_openapi.py` fetches
the file the catalog's `operationRef` points at and reads the field names out
of it, the same way SATHI and SHC already do. Nothing about KCC's fields is
written in the tool.

### What the contract has to declare

KCC seals its wire bodies, so `requestBody` describes an envelope and says
nothing about fields. Three declarations close that gap, and they are now part
of the OAN contract vocabulary rather than anything KCC-specific:

| Declaration | Where | What it says |
|---|---|---|
| `x-oan-request-attributes` | on the operation | the attributes a Beckn call must carry. Read instead of `requestBody` where present |
| `x-oan-from` | on a property | where the value comes from: `subject.mobile`, `challenge.otp`, or `session` |
| `const` | on a property | fixed by the spec, so the tool fills it without being told (`otpFor: krishika`) |

A required property with neither `x-oan-from` nor `const` is a gap in the
contract, and the tool refuses the call naming the field rather than sending a
body KCC will reject.

Because the fields are the provider's own, both mappings are pass-through: they
quote what arrived and cast where KCC's contract asks for a number. A field KCC
adds arrives with a spec change, not a mapping change.

### Reading a spec that is not published yet

`PROVIDER_OPENAPI_URL_REWRITE` ("from=to,from=to") redirects the fetch without
touching the catalog, which keeps naming the published address. A local stack
sets it to

    https://openagrinet.github.io/network-specs/=http://spec-files/

so the `spec-files` container serves the `network-specs` working tree. Same idea
as the provider base URL: deployment wiring, not a catalog or tool concern.

## What gets built

### beckn-onix

| File | Change |
|---|---|
| `internal/common/envelope/cbcfixed.go` | new — AES-256-CBC, fixed key, hex |
| `internal/common/envelope/envelope.go` | register the kind; give `decodeSealed` an encoding; `envelopeResponseOptional` |
| `internal/common/token.go` | `tokenEncoding: form`; `tokenExtraFields` |
| `internal/common/auth.go` | parse and validate the three new settings |
| `Grievance/*` | **none** |
| every other plugin | **none** |

### network-specs

The five steps above, plus the catalog is a helmcharts file rather than one here.

### helmcharts

- `config/adapters/providers/kcc.yaml.tmpl` — as above.
- `config/mappings/kcc/{loan.init,loan.status}.yaml`.
- `bin/setup.py` — a `kcc` entry: one capability at `v0.2`, two actions, both
  `retries: 0`. `init` would send a second OTP; `status` would spend a
  single-use one.
- `capability-examples/SchemeApplication/publish/ondemand-kcc.json`, and two
  by-hand bodies under `init/` and `status/`.
- `api-collection` — a **KCC** folder: the registry reads, the two-call journey
  on one `transactionId`, and the refusals.
- `.env.example` — `KCC_BASE_URL`, `KCC_TOKEN_URL`, `KCC_ENTITY_TYPE`,
  `KCC_USER_ID`, `KCC_PASSWORD`, `KCC_ENCRYPTION_KEY`, `KCC_PARTICIPANT_ID`,
  `KCC_MAPPING_BASE_URL`, `KCC_BECKN_VERSION`.
- `mock-server/mockkcc` — the sixth mock: login, encrypted OTP, encrypted
  status.

### bharat-oan-api

`agents/tools/v2/kcc.py` — three tools, same names and signatures as
`agents/tools/kcc.py`, behind `KCC_BECKN_VERSION` in `agents/tools/__init__.py`,
defaulting to `v1`. All three move on one flag: they share one OTP, so a farmer
mid-journey on one version cannot finish on the other.

| Change from v1 | Why |
|---|---|
| `async`, `httpx` | every v2 tool is |
| Builds a v0.2 contract, not a Beckn 1.1.0 envelope | v1 carries inputs as `fulfillments[0].customer.person.tags[]`; v2 uses `commitmentAttributes` |
| Drops the `aif.py` helper imports | `_build_payload`, `_descriptor`, `_endpoint`, `_unwrap`, `_values` are all v1 envelope readers |
| Keeps `_is_rejected`, `_is_approved`, `_rupees`, `_render_application` | rendering and the DRAFT-hides-rejection quirk are domain knowledge and are unchanged |
| Keeps `_loggable`'s masking | the OTP must not reach a log |
| Keeps all three input guards | mobile, exactly-six-digit OTP, numeric application number — all fire before any round trip, as on v1 |
| Adds an application cache | `select` has no upstream call |
| Drops nothing else | |

## Order of work

| # | Step | Depends on |
|---|---|---|
| 1 | `aesCbcFixedKey` codec + `envelopeResponseOptional` + tests | — |
| 2 | `tokenEncoding: form` + `tokenExtraFields` + tests | — |
| 3 | Promote the OpenAPI; write the two real mappings | — |
| 4 | Provider template, `setup.py`, catalog, env | 1–3 |
| 5 | `mockkcc` | 3, so the cipher is agreed in one place |
| 6 | By-hand journey through the adapter, plus the refusals | 4, 5 |
| 7 | `agents/tools/v2/kcc.py`, flag off | 6 |
| 8 | Tool-level run, then the newman folder | 7 |
| 9 | Flip `KCC_BECKN_VERSION=v2`; `quick-try.md`; the README row | 8 |

1, 2 and 3 are independent and can run together.

## Open items

**The only real blocker.**

1. **The AES key and the IV rule are unknown, and no mock can settle them.**
   The draft says AES-256-CBC and nothing more: is the IV the key, is it
   prefixed to the ciphertext, is it zero? The mock and the codec are built
   from the same guess, so they will agree with each other whatever that guess
   is. Everything else in this plan can be proven locally; this cannot. When
   credentials arrive, this is the one thing to check first, and it is the
   likeliest cause of any failure that the error table in `quick-try.md` does
   not explain.

**Waiting on access, not on a decision.**

2. **No credentials.** `KCC_ENTITY_TYPE`, `KCC_USER_ID`, `KCC_PASSWORD` are in
   none of our repos, and we have no KCC contact to ask. Swapping the mock for
   the real host is a `.env` edit and one
   `make register-provider PROVIDER=kcc UPDATE=1`. No code, no rebuild.

3. **The host is production.** `fasalrin.gov.in`, not a UAT host like KHC's.
   Any live test sends a real SMS to whoever owns that number. Nothing should
   reach it except with somebody's own registered number.

**Settled by design — listed so nobody re-opens them.**

4. **What a failure body looks like.** Was open item 1. We cannot ask, so the
   plan no longer depends on the answer: `envelopeResponseOptional` (gap 2)
   makes both shapes work. Worth confirming from a real call one day; worth
   blocking on, no.

5. **Can `applicationStatus` be called twice on one `requestID` + OTP?** We
   assume **no** and cache the list tool-side, which is correct either way. If
   the answer turns out to be yes, `select` can become a real `select` action
   later without changing anything else.

6. **`tokenTtlField`** for a relative lifetime is deferred to `tokenTtl: 1h`.
   Revisit if KCC's `sessionTTL` ever stops being 3600.

7. **`operationRef` resolution in the adapter** is deferred, exactly as for
   AIF — method and path keep coming from the registry row. The **tool** does
   resolve it, so KCC's field names are read from the contract at runtime and
   are written nowhere in the code.

**Closed by the contract-driven work.**

8. **Field names in the tool.** Was a real gap: the tool held KCC's field names
   as constants, so a spec change meant a code change. Closed by
   `x-oan-request-attributes` and `x-oan-from` on the operations, which the
   tool reads. A required field with no hint is now refused loudly, naming the
   field, rather than sent short.

9. **Mapping-level renaming.** Both mappings were translating Beckn names into
   KCC names, which made the mapping a second vocabulary to keep in step. They
   are pass-through now; the only thing either does to a value is cast the OTP
   to the number KCC's contract asks for.

**Found by running the agent, not the tool.**

12. **The agent skips `initiate_kcc_otp` on some phrasings and says an OTP was
    sent anyway.** Three of eight farmer phrasings never call the tool and
    still reply *"An OTP has been sent to your mobile XXXXXX3210. It is valid
    for 15 minutes."* — 0/3 runs each for `check kcc application status for
    <number>`, `check my kcc loan status for mobile <number>` and
    `<number> - kcc loan application status`. The other five call it 3/3. The
    farmer then waits for an SMS that was never requested. Reproduced
    identically on **v1**, so this is not a v2 regression; the one-line
    `status_*.md` step 2 example is the likely cause — it hands the model a
    finished sentence with a literal masked number, and v1 copied that
    number (`XXXXXX6386`) for a farmer whose number ended `3210`. Fixing it
    means rewriting step 2 in all thirteen `status_*.md` files to describe the
    reply instead of spelling it out. Not done here: it is a prompt change
    across every language, and it is wrong on v1 too.

**Open, and not KCC's to close.**

10. **The response side is still not contract-driven.** The request path reads
    every field name from KCC's OpenAPI. The reply does not: the tool reads
    `applications`, `applicationNo`, `status`, `requestedAmount`,
    `sanctionedAmount`, `rejectionReason`, `bank` and `updatedOn` by name,
    because the *mapping* defines the response shape and nothing declares it.
    Every v2 tool does the same — SATHI reads `bags` and `dealer_id`, AIF reads
    `loanApplicationNumber`, PM-KISAN reads `ticketNo`. Closing it needs new
    vocabulary across all six providers, so it is carried rather than fixed
    here — [README](../README.md#carried-to-the-next-phase), item 5.

11. **`AgricultureResource/v0.2`'s fix is on the wrong branch for SATHI.** The
    `x-oan-schema-by-action` entry this work added is on `bv-migration`;
    SATHI's catalog `@context` names `bv-features`, which still refuses every
    `select`. Verified locally through a `spec-files` context rewrite, not
    through the published pack — [README](../README.md#carried-to-the-next-phase),
    item 4.

## Risks

1. **The cipher is the whole flow, and we cannot test it.** A wrong IV rule
   produces bytes KCC rejects with whatever its generic error is, and a mock
   built on the same wrong assumption passes every test we write. This is open
   item 1, and with no KCC contact it stays open until credentials arrive.
   Everything else ships proven; this ships assumed. Say so when handing over.
2. **A refusal that reads as an outage.** Handled by gap 2
   (`envelopeResponseOptional`), and listed here because it is what goes wrong
   if that flag is forgotten on the KCC binding: the farmer is told to come
   back later instead of to retype the code. Worse than failing.
3. **The in-process application cache dies with the process.** The farmer must
   ask for a new OTP. `v2/pmfby_scheme_status.py` already has this property, so
   it is not new, but it does make `select` best-effort rather than guaranteed.
4. **Three response shapes on one field.** The decrypted `data` can be one
   application, an array, or `{applications: […]}`. A mapping that handles only
   the common one passes every test written against the mock and fails on a
   farmer with exactly one application. Handle all three from the start — and
   note the `[[]]` trap: `[response.data]` where `data` is `[]` yields `[[]]`,
   not `[]`. Guard with `$exists(...[0]) ? […] : []`, as the PMFBY mappings now
   do.
5. **Production host, real SMS.** Stated under open items and repeated here
   because it is the one mistake that reaches a stranger's phone.
