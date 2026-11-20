# PM-KISAN: v1 -> v2, against a mock provider

PM-KISAN is the third capability to move, after mandi and weather. It is the
first one that is **not** a stateless lookup: every call is OTP-gated, and the
OTP taken in one request has to still be good in the next. It is also the first
one we will prove against a **mock upstream** rather than the real service,
because we do not hold PM-KISAN credentials.

Both of those are departures from how mandi and weather were done, and both are
deliberate. This document says what we get for them and what we give up.

## Which tools migrate

Five tools, in two modules, 1667 lines. They are registered in
`agents/tools/__init__.py` and are what a farmer actually reaches.

| v1 tool | Use case, in the farmer's words | v1 call | v2 action |
|---|---|---|---|
| `initiate_pm_kisan_status_check` | "Has my PM-KISAN instalment come?" — step 1, send me an OTP | `POST {BAP_ENDPOINT}/init` | `init` |
| `check_pm_kisan_status_with_otp` | step 2, here is the OTP, now tell me | `POST {BAP_ENDPOINT}/status` | `status` |
| `pmkisan_grievance_send_otp` | "Something is wrong with my payment" / "What happened to my complaint?" — send me an OTP | `POST {BAP_ENDPOINT}/init` | `init` |
| `pmkisan_submit_grievance` | file the complaint | `POST {BAP_ENDPOINT}/init` | `support` |
| `pmkisan_grievance_status` | "What happened to my complaint?" | `POST {BAP_ENDPOINT}/status` then `/search` | `status` |

Two distinct farmer journeys:

1. **Scheme application status.** A farmer identifies themselves by PM-KISAN
   registration number (`TN000000000` — two letters, nine digits) *or* by their
   registered 10-digit mobile. They get an OTP, give it back, and we tell them
   their beneficiary record and, if payment has not arrived, what is blocking
   it. The blocker is a *second* upstream read (`/ChatbotBeneficiaryStatus`).
2. **Grievance.** Same OTP gate, then either lodge a complaint against a
   category from `assets/grievance_types.json` (overridable with
   `GRIEVANCE_TYPES_PATH`), or read back a complaint already filed.

### Two of these five are switched off in production today

`pmkisan_grievance.py:73` defines a "temporarily unavailable" message, and
`:809` and `:870` return it early. So `pmkisan_submit_grievance` does nothing,
and `pmkisan_grievance_send_otp` does nothing when called with
`purpose="submit_grievance"`. The comment calls it a "temporary PM-KISAN
grievance-lodge stop" and says to re-enable by removing the two guards.

This is good news for a mock-first migration, not bad. The lodge path is the
only one of the five that **writes** to a government system, and it is the one
we would least want to exercise by hand against production. Right now nobody is
using it, so we can build and prove it against the mock with nothing at stake,
and leave the guards in place in the v2 module until someone decides to lift
them. **Do not remove those guards as part of this migration.** Whatever took
the lodge path down is not ours to overrule, and we have not asked why.

## What v2 changes for PM-KISAN

The adapter work is already done. This is the part that makes PM-KISAN the
cheapest remaining migration, and it is worth being precise about, because I
previously told you this capability needed the most new work and that was
wrong.

| Piece | Where | State |
|---|---|---|
| Plugin | `beckn-onix/.../Grievance/` | **in the running 0.2.0 image** — `docker exec network-adapter ls /app/plugins` lists `Grievance.so` |
| OTP as the v2 `challenge` | `Grievance/otp.go` | done |
| Second upstream read after success | `Grievance/followup.go` | done |
| AES-GCM envelope | `internal/common/envelope/gcm.go` | done |
| AES-128-CBC, key in band | `internal/common/envelope/cbc.go` | done |
| Adapter provider config | `config/adapters/providers/pmkisan.yaml.tmpl` | written |
| Mappings | `config/mappings/pmkisan/*.yaml` | all 5 |
| `init`/`support`/`status` routing | `routing-network-adapter-consumer.yaml` | already routed |
| Schema packs | `Grievance/v0.1`, `SchemeApplication/v0.1` | published, HTTP 200, and **PM-KISAN-specific schemas already in both** |
| Provider entry | `bin/setup.py` `PROVIDERS["pmkisan"]` | present, both capabilities, 5 actions |

There is **nothing to build in beckn-onix**. The remaining work is a mock, some
config, examples, and the two tool modules.

### The packs are per-scheme, not generic

Both packs are OpenAPI 3.1.1 and resolve from the URL `setup.py:462` builds:
`https://openagrinet.github.io/network-specs/schema/<Capability>/v0.1/attributes.yaml`.
Both return 200. What they contain is one flat schema **per scheme per call**:

| Pack | PM-KISAN schemas | Covers |
|---|---|---|
| `Grievance/v0.1` | `PMKISANCatalog`, `PMKISANInit`, `PMKISANStatus`, `PMKISANSupport` | all four grievance calls |
| `SchemeApplication/v0.1` | `PMKISANApplicationCatalog`, `PMKISANApplicationInit`, `PMKISANApplicationStatus` | all three application calls |

So every action this migration needs is already described. Each schema names
its `mustSend`, `maySend` and `comesBack` fields explicitly, which is what the
v2 tool has to satisfy and what will decide which v1 fields survive.

The packs state their own rule: *"One scheme per payload, named by scheme.code.
Each scheme defines one flat schema per call it serves and nothing else, so a
new scheme or a new call is an addition and never an edit."*

**That corrects something I said earlier.** I suggested these packs looked
scheme-generic, so KCC, AIF and KHC might ride them with no schema work and the
migratable total might be 27 tools rather than 11. That is wrong. The packs are
explicitly per-scheme. Adding another scheme is an *addition* — cheap and
non-breaking, by design — but it is real work in `network-specs`, not free.

### Work moves OUT of the tool this time

Mandi was the opposite — v2 pushed ranking and formatting work *into* the tool.
PM-KISAN goes the other way, and by a lot:

- **v1 verifies the OTP itself.** `pmkisan_grievance_status` posts to `/status`
  to check the OTP, inspects the reply with `_is_otp_verified`, and only then
  posts to `/search` for the actual answer. Two round trips and a hand-written
  state check in Python.
- **v2 does not.** `otpVerifyPath: /ChatbotOTPVerified` and
  `otpVerifyActions: "support,status"` in the adapter config mean the adapter
  verifies before it forwards. The tool sends one request.
- **v1 builds the encryption.** v2's envelope config (`envelopeKeyEnv`,
  `envelopeNonceEnv`, `envelopeTokenEnv`) means the tool never sees a key.
- **v1 makes the blocker call.** v2's `followUpPath: /ChatbotBeneficiaryStatus`
  means the adapter does it and returns the answer under `_beneficiaryStatus`.

So expect the v2 modules to be meaningfully **shorter** than the 1667 lines
they replace. That is a claim to check at the end, not an assumption to design
around.

## Why a mock, and what a mock is worth

We do not have PM-KISAN credentials. Without them the adapter cannot reach the
provider, so nothing downstream can be tried at all — not the routing, not the
envelopes, not the OTP state machine, not the tools. A mock unblocks every one
of those, and the swap to the real service later is a registry write
(`make register-provider PROVIDER=pmkisan UPDATE=1`), not a code change,
because the upstream address lives in the `ProviderSchema` row.

This follows the house pattern. `mock-server/` already holds `mockimd` and
`mockagmarknet`, and `mock-server/README.md` states the rule they are built to:

> A mock that answered tidily would let a mapping pass here and fail against the
> real service, so both reproduce the awkward parts on purpose.

`mockpmkisan` must be held to the same standard.

### What the mock proves

Routing and binding keys. Schema validation against the published packs. Both
AES envelopes round-tripping. The OTP state machine across `init` -> `support`
and `init` -> `status`, including that a *wrong* OTP is refused. The follow-up
call firing and landing under the right field. Every mapping transform. The
full v2 tool wiring, end to end, from tool call to answer.

That is most of the surface area, and none of it can be tried today.

### What the mock does not prove

Everything about PM-KISAN itself: the real field spellings, the real error
bodies, what it does on no-data, its auth, its rate limits, and whether our
encryption parameters match theirs.

The evidence that we are guessing is in our own mappings. All five carry this:

> *the first of `Rsponce`, `Responce`, `Status`, `status` that is present*

Four spellings with a fallback chain, because nobody knew which one the real
portal sends. A mock we write cannot settle that — it can only return whichever
one we tell it to.

**So this migration cannot be signed off as "PM-KISAN works".** It can be signed
off as "the v2 path works, pending the real provider". That distinction goes in
the commit message and in `quick-try.md`, and the capability does not get
pointed at production traffic until it has been tried against the real host.

## The mock

`mock-server/mockpmkisan`, Go, same shape as `mockimd` — single `main.go`,
flags for behaviour, no dependencies beyond the standard library.

### Endpoints

| Path | Used by | Behaviour |
|---|---|---|
| `/ChatbotOTP` | both `init` actions | issue an OTP, remember it against the identifier |
| `/ChatbotOTPVerified` | adapter, before `support`/`status` | verify; **must refuse a wrong OTP** |
| `/LodgeGrievance` | `grievance.support` | file, return a ticket number |
| `/GrievanceStatusCheck` | `grievance.status` | read back by registration number |
| `/ChatbotUserDetails` | `application-status.status` | the beneficiary record |
| `/ChatbotBeneficiaryStatus` | adapter follow-up | what is blocking payment |

### Envelopes

Both schemes are fully specified in beckn-onix, so the mock mirrors them
exactly rather than inventing anything:

- **AES-GCM** (`gcm.go:24`) — fixed key and nonce from env, `base64(ciphertext||tag)`.
  Used for Grievance. Note `gcm.go:25`: *"A fixed nonce breaks GCM; TLS is the
  only protection (ADR-0001)."* We are reproducing a known-weak scheme because
  the provider requires it; the mock should not quietly improve on it.
- **AES-128-CBC, key in band** (`cbc.go:26`) — a fresh key per request sent as
  `cipher@key`, PKCS#7 padded, base64. Used for SchemeApplication and every OTP
  exchange.

Request field `EncryptedRequest`; responses read from `d.output` then `output`;
token carried as `TokenNo` (service) or `Token` (OTP).

### What it must deliberately get wrong

Per the README's rule, a tidy mock is a useless mock. At minimum:

1. **The yes/no flag spelling is a flag.** `-flagfield=Rsponce|Responce|Status|status`,
   so the suite can run against each and the fallback chain is actually proven
   rather than assumed. This does not tell us which one is real. It does tell us
   that whichever it is, we handle it.
2. **Refuse a wrong OTP, and expire a right one on a settable window.**
   `-otpwindow=10m`, so the ten minutes we hardcode can be set to something
   shorter and the expiry path exercised instead of always passing. This cannot
   tell us the real window. It can prove we behave sanely at whatever it is.
3. **Return a record with the blocker absent**, not empty — `followup.go` is
   explicit that on failure the field is *absent, never empty (ADR-0005)*, and a
   mapping that cannot tell those apart should fail here.
4. **Return plaintext on some failures.** `TestPMKISANSupportTreatsPlainTextFromThePortalAsARefusal`
   exists because the real portal does this. The mock should too.
5. **Derive answers from the input.** A wrong registration number should produce
   visibly wrong output rather than passing silently, as `mockagmarknet` does
   with market codes.

### How it is built and run

Compose pulls every image and builds nothing. `mockpmkisan` follows
`mockimd`: sources live in `mock-server/`, built once, `pull_policy: missing`,
on the `oan-internal` network only, with a loopback-published port for
debugging. New `.env` keys: `MOCKPMKISAN_IMAGE`, `MOCKPMKISAN_PORT`, plus the
key/IV/token values the envelope config names.

It is **not** a `depends_on` of the adapter, for the same reason `mock-imd` and
`mock-agmarknet` are not (`docker-compose.yml:570`): starting the adapter should
not drag mocks up. It is started by name.

## The recipe

### Phase 0 — mock first

Write `mock-server/mockpmkisan`. Build it locally. Hit all six endpoints with
`curl` and a small script, with no adapter in the path, and confirm both
envelopes round-trip. **Nothing below is worth starting until this passes.**

### Phase 1 — wire it in

Compose service. `.env` keys, pointing `PMKISAN_BASE_URL` and
`PMKISAN_CHATBOT_BASE_URL` at the mock. Locally generated key and IV. Then
`make register-provider PROVIDER=pmkisan` — `setup.py:596` splices
`pmkisan.yaml.tmpl` into the adapter config from the registry bindings, so the
Grievance step should appear without anyone editing the adapter yaml.

Check it with `make check-mappings` before anything else: all five PM-KISAN
mapping URLs must resolve, or a select fails later with a
`NET_INTERNAL_ERROR` that does not name the URL.

### Phase 2 — prove the network path, no AI involved

`capability-examples/Grievance/` and `capability-examples/SchemeApplication/`
do not exist and have to be written — publish, discover, init, support, status.
**Done, with the exception of publish and discover:** the five written are the
five that were actually sent and answered. A publish or discover body would
have had to be invented, and an unsent example is worse than a missing one.
Mandi and weather both have these and they are how a capability gets proven by
hand.

Then, by hand, both journeys:

- application status: `init` -> OTP -> `status`, and confirm `_beneficiaryStatus` arrives
- grievance: `init` -> OTP -> `support` -> `status`
- and the negative: a wrong OTP is refused at `/ChatbotOTPVerified`, by the
  adapter, before the mapping runs

### Phase 3 — swap the tools

`pmkisan_scheme_status_v2.py` and `pmkisan_grievance_v2.py`, behind the same
flag pattern mandi and weather use: an env var read at import time in
`agents/tools/__init__.py`, selecting between two modules that export the same
function name, signature and docstring, so neither the agent nor the skill
prompt changes. Here that is `PMKISAN_BECKN_VERSION`, defaulting to `v1` until
this is signed off.

Diff every answer against the v1 tool running against the same mock. **Expect
the outputs to differ, and do not treat that as a failure on its own.** Mandi's
two versions are byte-identical; weather's are not, because the v2 provider
mapping drops fields the v0.1 schema pack cannot hold
(`agents/tools/__init__.py:66-69`). PM-KISAN is more likely to behave like
weather than like mandi — the Grievance pack carries 16 terms and
SchemeApplication 10, and the v1 tools return whatever the portal sent. Each
dropped field needs a decision recorded here: acceptable loss, or a gap in the
pack.

Keep the lodge guards. They stay as they are in v1.

### Phase 4 — write down what is unproven

`quick-try.md` for the next person, and an open-questions section here, stating
plainly that this was proven against a mock and listing exactly what the real
provider could still break.

## What actually happened — done 2026-11-20

All five phases ran. The flag is `PMKISAN_BECKN_VERSION`, read at import time
in `agents/tools/__init__.py`, **defaulting to `v1`**. Both capabilities move
together behind the one flag because they share an OTP: a farmer who asked for
one on v1 cannot spend it on v2.

### Evidence

| what was run | result |
|---|---|
| provider leg, chained (`init` -> OTP -> `support` -> `status`) | 10 passed, 0 failed |
| the five shipped `capability-examples` bodies, sent as shipped | 5 passed, 0 failed |
| tool layer, driven as the agent drives it, inside `sva_app` | 15 passed, 0 failed |
| import + tool registration, on `v1` and on `v2` | 55 tools either way |
| pmkisan + mandi tests, on `v1` and on `v2` | 141 passed each |
| full suite, with the change | 12 failed, 1302 passed |
| full suite, baseline with the change stashed | **12 failed, 1302 passed — identical** |

The twelve failures are pre-existing and unrelated (`test_backend_owned_telemetry.py`,
`test_callback_router.py` wanting a live service, `test_model_registry_providers.py`).
Zero regressions.

The provider-leg run is **chained, not replayed**, and that matters: a fixture
replay cannot prove the grievance status read, because the fixture's `filedOn`
is from whenever it was written, so it lands on the not-found path by accident
and the real path never runs. The chained run files a grievance, keeps the
`filedOn` the provider returned, and reads it straight back.

What it saw: `ticketNo=927047`, `filedOn=2026-11-20`, state `UnderReview`;
`latestInstalmentPaid=8` **as an int**, blockers
`['IncomeTaxPayee','LandSeedingPending']`, `registeredOn=2019-02-24T00:00:00+05:30`.
Both refusals — wrong OTP, unknown registration — came back as genuine
`BIZ_GENERIC_ERROR`s.

### Two findings that change what v2 can do

Both were proven by sending requests, not inferred from reading schema.

**1. Thirteen of the twenty grievance types cannot be filed on v2.** The v1
tool sends codes `G011`-`G030`. The v2 pack hard-enums `G001`-`G010` and
rejects anything else with `SCH_INVALID_ENUM`. Sending `G003` returns 200;
`G011` and `G030` return 400. These are **different taxonomies, not a
renumbering** — v1's are specific complaints, v2's are the ten headings the
portal's own form offers.

Seven labels have a published heading meaning the same thing and are mapped.
The other thirteen — the wrongly-marked-deceased family, the pending-approval
family, land-detail and Aadhaar-seeding problems, `CORRUPTION`, `URGENT` —
have no equivalent. They are **refused with the reason said out loud** rather
than filed under a heading that merely sounds close. A farmer reporting
corruption must not be routed to "Payment related".

This is survivable only because **`pmkisan_submit_grievance` is already
disabled in production** by `_PMKISAN_GRIEVANCE_SUBMIT_DISABLED_MSG`, and that
guard is carried into v2 unchanged. The gap blocks a tool that is already
switched off. It must be closed before lodging is ever re-enabled.

**2. `pmkisan_grievance_status` now needs a filing date.** v2 reads *one*
grievance where v1 listed them all: `PMKISANStatus` requires `case`, and
`case` requires `filedOn` (the mapping filters
`$filter(... = $ca.case.filedOn)[0]`). So the v2 tool gained a `filed_on`
parameter the v1 tool never had — the only signature difference between the
two versions. The agent has to ask the farmer for the date; it was shown when
the grievance was submitted. When no record matches, the tool **names the date
back** rather than leaving the farmer guessing which of the two inputs was
wrong.

### Output differs from v1, by design

As predicted, PM-KISAN behaves like weather and not like mandi: the two
versions' answers are **not** byte-comparable. v1 rendered the gateway's tag
soup; v2 renders named fields from the v0.1 pack. One case worth naming —
`application.blockers` **absent** means the follow-up read failed (ADR-0005),
**present and empty** means PM-KISAN checked and found nothing. The tool does
not conflate them: empty says "nothing is blocking payment", absent says
nothing at all.

## Risks

1. **The envelope parameters may not match the real service.** Key length, IV
   handling and padding are our reading of the provider's scheme. The mock will
   agree with us by construction. This is the single largest thing the mock
   cannot catch.
2. **The flag-spelling fallback stays a guess.** Proven to work for all four
   candidates, proven correct for none.
3. **We invent the OTP validity window and report it to the farmer as fact.**
   `grievance.init.yaml:28` hardcodes `$otpWindowMillis := 10 * 60 * 1000` and
   writes `expiresAt` from it. Nothing in PM-KISAN's reply says ten minutes —
   that number is ours. If the real window is shorter, farmers are told an
   expired OTP is still good; if longer, they are rushed for no reason.

   (`otpVerifyTimeoutMs: "10000"` is **not** related. It is the HTTP timeout on
   the verify call — `envelope.go:52` and `:259`. An earlier draft of this
   document conflated the two.)
4. **It is still a stateful flow.** v2's discover/select split has only been
   proven on stateless lookups. The adapter has the machinery, but it has never
   been run against anything.

## Not in scope

- **PMFBY**, but see the open question below — it may deserve to go first.
- **Re-enabling the grievance lodge path.**
- **KnowledgeAdvisory**, by decision, goes last.
- **AgricultureFacility / PoCRA** — no BV tool calls it, so it is a new feature
  rather than a migration.

## Open question: should PMFBY grievance go first?

Raised on 2026-11-20, while checking this plan. It is not settled, and this
plan is written for PM-KISAN either way.

PMFBY looked like the weaker candidate because it has no provider template, no
`PROVIDERS` entry, and no env keys — not even in `.env.example`. Reading the
Grievance pack properly changes that. The pack already carries `PMFBYCatalog`,
`PMFBYStatus` and `PMFBYSupport` — and, tellingly, **no `PMFBYInit`**. PMFBY's
grievance flow has no OTP at all, which is exactly what its mapping says:
*"Files a grievance; no OTP."*

Follow that through and PMFBY grievance is far simpler than PM-KISAN:

| | PM-KISAN | PMFBY grievance |
|---|---|---|
| OTP state machine | yes | **none** |
| Encryption | AES-GCM **and** AES-128-CBC | **none** — grep for token/auth/header/encrypt across both mappings returns nothing |
| Follow-up second call | yes | no |
| Mappings needed / present | 5 / 5 | 2 / 2 |
| Pack schemas | present | present |
| Still missing | env keys, mock | provider template, `PROVIDERS` entry, base URL, mock |

So PMFBY grievance is plain JSON over the v2 network path with no cryptography
and no state. That makes it a cleaner first experiment: it isolates *"does a
Grievance capability work end to end over v2"* from *"is our AES and OTP
guesswork right"*. If PMFBY passes and PM-KISAN then fails, the fault is in the
envelope or the OTP — which is precisely where the risk is concentrated.

The case against going PMFBY-first is that it delivers far less. **Checked
since: PMFBY migrates 2 tools of 6, not 4.** `initiate_pmfby_grievance_otp` and
`check_pmfby_grievance_otp` are real, working OTP tools — and they are exactly
the ones with nowhere to go, because the pack has no `PMFBYInit`.
`pmfby_scheme_status.py`'s other 2 have no PMFBY schema in
`SchemeApplication/v0.1` at all. So PM-KISAN delivers 3 live tools against
PMFBY's 2, and one of PMFBY's two loses its OTP gate on the way.

That OTP gap is a decision someone has to make before PMFBY's filing tool can
ship, and it is set out in `../pmfby/plan.md`. It does not block using PMFBY as
the **proving ground** — phases 0 to 2 are unaffected — but it does mean
PMFBY-first buys confidence rather than delivery.

The branch named `feat/57-pmfby-mock-and-registry-seed` does **not** contain a
PMFBY mock, despite the name; its only mocks are `mockagmarknet` and
`mockimd`.
