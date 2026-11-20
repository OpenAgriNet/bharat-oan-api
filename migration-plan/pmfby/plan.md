# PMFBY: v1 -> v2, against a mock provider

PMFBY (Pradhan Mantri Fasal Bima Yojana — crop insurance) shares the
`Grievance` plugin and pack with PM-KISAN. It is proposed as the **first**
capability to prove over v2's `init`/`support`/`status` actions, ahead of
PM-KISAN, because its grievance path has no OTP and no encryption. See
`../pmkisan/plan.md` for why that ordering was raised.

This document exists to say which of PMFBY's six tools can actually move, and
the honest answer is **two**. The other four have no v2 schema to move to.

## The relevant tools

Six tools, two modules, 1494 lines, all registered in `agents/tools/__init__.py`.

| v1 tool | Use case, in the farmer's words | v1 call | v2 action | Can it move today? |
|---|---|---|---|---|
| `pmfby_grievance_status` | "What happened to my complaint?" | `/search` | `status` | **Yes** — clean |
| `pmfby_submit_grievance` | "My crop claim is wrong, log a complaint" | `/init` | `support` | **Yes, but** it loses the OTP gate |
| `initiate_pmfby_grievance_otp` | step 1 of filing — send me an OTP | `/init` | — | **No** |
| `check_pmfby_grievance_otp` | step 2 — here is the OTP | `/status` | — | **No** |
| `initiate_pmfby_status_check` | "Is my policy active / where is my claim?" — send OTP | `/init` | — | **No** |
| `check_pmfby_status_with_otp` | step 2 — here is the OTP | `/status` | — | **No** |

### Why four of them cannot move

**`initiate_pmfby_grievance_otp` and `check_pmfby_grievance_otp`** have no
target because the `Grievance/v0.1` pack defines `PMFBYCatalog`, `PMFBYStatus`
and `PMFBYSupport` — and **no `PMFBYInit`**. v2 models an OTP as the `init`
action issuing a `challenge`; with no `PMFBYInit` schema there is no v2 call
for these two tools to become.

**`initiate_pmfby_status_check` and `check_pmfby_status_with_otp`** have no
target because `SchemeApplication/v0.1` defines only `PMKISANApplicationCatalog`,
`PMKISANApplicationInit` and `PMKISANApplicationStatus`. There is no PMFBY
schema in that pack at all. This is a `network-specs` addition, not a config
gap, and it blocks these two regardless of what we do here.

So this migration covers **2 of 6 tools**. That number should be in the commit
message. It would be easy to describe this as "migrating PMFBY grievance" and
leave the reader to discover that two thirds of PMFBY is untouched.

**This corrects something I said while reviewing the PM-KISAN plan.** I wrote
that PMFBY grievance had four live tools against PM-KISAN's three, and flagged
the OTP tool names as needing a check. Having now read them: they are real OTP
tools, they work, and they are exactly the ones with nowhere to go. The count
is 2, not 4, and the comparison I drew was wrong.

## The OTP discrepancy, which is the real decision here

v1 and v2 disagree about whether filing a PMFBY grievance needs an OTP, and
they disagree in the direction that matters.

**v1 requires one.** `initiate_pmfby_grievance_otp` sends a 6-digit OTP to the
registered mobile. `check_pmfby_grievance_otp` verifies it via BAP `/status`
and will not pass without `otp_verified` plus a real policy response. Its
docstring is explicit: *"Do not ask for application number or complaint details
before OTP is verified"* and *"Do not proceed to collect grievance details
unless this tool returns OTP verified."* `pmfby_submit_grievance` takes `otp`
as its first argument.

**v2 does not.** `grievance.support.yaml` says *"Files a grievance; no OTP."*
`PMFBYSupport` requires `informationMode, scheme.code, provider.id,
provider.descriptor.name, enrolmentId, applicantMobile, cropYear, season,
grievance.description` — and nothing resembling a challenge. A grep across both
PMFBY mappings for token, auth, header, encrypt or envelope returns nothing.

So a straight migration of `pmfby_submit_grievance` means **anyone who knows a
farmer's mobile number, application number, year and season can file a
grievance in their name.** Today they would also need an OTP sent to that
farmer's phone.

This is not a thing to decide while writing a mock. Three ways out, in the
order I would argue for them:

1. **Add `PMFBYInit` to the pack** and keep the OTP. Honest, matches v1
   behaviour, and the pack's own rule says *"a new scheme or a new call is an
   addition and never an edit"* — so it is designed to accept this. Cost: a
   `network-specs` change and a third mapping. This is the option I would take.
2. **Ship `pmfby_grievance_status` only**, leave filing on v1. Smallest, safest,
   proves the v2 path, regresses nothing. Delivers 1 tool.
3. **Accept the drop.** Only with someone's explicit sign-off, named in this
   file. Do not do this quietly because the mapping already says "no OTP" — that
   line records a design decision whose rationale nobody here has seen.

**Nothing in Phase 3 ships until this is answered.** Phases 0–2 are unaffected
and can proceed now, because the network path is the same either way.

Note that `pmfby_grievance_status` has no such problem. Its docstring says
*"No OTP is required for status lookup"* and v2 agrees. That one is a clean
like-for-like move.

## What exists and what is missing

| Piece | State |
|---|---|
| `Grievance` plugin | in the running 0.2.0 image — `docker exec network-adapter ls /app/plugins` lists `Grievance.so` |
| Pack schemas | `PMFBYCatalog`, `PMFBYStatus`, `PMFBYSupport` published, HTTP 200 |
| Mappings | both present: `grievance.status.yaml`, `grievance.support.yaml` |
| `init`/`support`/`status` routing | already routed (`routing-network-adapter-consumer.yaml`) |
| Adapter provider template | **missing** — no `config/adapters/providers/pmfby.yaml.tmpl` |
| `setup.py` `PROVIDERS` entry | **missing** — only `pmkisan` exists |
| Env keys | **missing** — no `PMFBY_*` in `.env`, and none in `.env.example` either |
| Mock | **missing** |
| `capability-examples/Grievance` | **missing** (shared gap with PM-KISAN) |

The upstream, from the mapping headers, is a PMFBY KRPH API:

| Action | Path |
|---|---|
| `support` | `POST /krphapi/FGMS/AddKRPHNCIPGrievenceSupportTicket` |
| `status` | `POST /krphapi/FGMS/GetGrievenceTicketsStatus` |

"Grievence" is PMFBY's spelling, not a typo in our code, and the mappings say so.

### The provider template should be nearly empty

`pmkisan.yaml.tmpl` is 38 lines of envelope and OTP configuration. PMFBY needs
none of it — no `envelope`, no `otpEnvelope`, no `otpVerifyPath`, no
`followUpPath`. It should be the `bindingKeys` / `providerIdAt` /
`capabilityCodeAt` triple plus `authScheme: none`, unless the real KRPH API
turns out to want a credential, which we cannot know without reaching it.

That near-emptiness is exactly why PMFBY is the better first experiment.

## Why a mock, and what it is worth

Same reasoning as PM-KISAN, and the same limits — see
`../pmkisan/plan.md` under "Why a mock, and what a mock is worth". We have no
PMFBY credentials and no confirmed base URL, so without a stand-in nothing
downstream of the adapter can be tried at all.

The swap to the real service later is a registry write
(`make register-provider PROVIDER=pmfby UPDATE=1`), not a code change, because
the upstream address lives in the `ProviderSchema` row.

**What the mock proves here:** routing, binding keys, schema validation against
the real published `PMFBYSupport` and `PMFBYStatus`, both mappings, and the
v2 tool wiring end to end.

**What it does not prove:** anything about the real KRPH API — its field
spellings, error bodies, no-data behaviour, auth, or rate limits. PMFBY has far
less of our guesswork in it than PM-KISAN does (no invented OTP window, no
assumed cipher parameters), so the gap is narrower, but it is the same gap.

**This cannot be signed off as "PMFBY works"** — only as "the v2 path works,
pending the real provider."

## The mock

`mock-server/mockpmfby`, Go, same shape as `mockimd` (147 lines, standard
library only, flags for behaviour).

| Path | Serves | Behaviour |
|---|---|---|
| `/krphapi/FGMS/AddKRPHNCIPGrievenceSupportTicket` | `support` | file a grievance, return a ticket number |
| `/krphapi/FGMS/GetGrievenceTicketsStatus` | `status` | read back by ticket number and mobile |

### What it must deliberately get wrong

Per `mock-server/README.md`: *"A mock that answered tidily would let a mapping
pass here and fail against the real service."* From what the mappings already
defend against:

1. **Return empty fields.** `grievance.status.yaml` drops empty PMFBY fields, so
   the mock must send some.
2. **Return a record that does not name the ticket asked for.** The mapping
   treats that as a 400 in our words, distinct from "no record" — two paths that
   a tidy mock would never exercise.
3. **Keep PMFBY's spelling.** `Grievence`, `requestorMobileNo`,
   `GrievenceSupportTicketNo`. A mock that silently corrected these would hide a
   mapping that had them wrong.
4. **Quote the request back in an error body.** The mapping deliberately strips
   that before logging and never returns it, and that stripping should be
   exercised rather than assumed.
5. **Derive the answer from the input**, so a wrong ticket number is visible
   rather than passing silently.

No encryption, no OTP, no token. If any of those turn out to be needed, that is
a finding about the real API and belongs in the open questions, not in the mock.

## The recipe

### Phase 0 — mock first
Write `mock-server/mockpmfby`. Build locally. Hit both endpoints with `curl`,
no adapter in the path. Smaller than PM-KISAN's mock by a wide margin: two
endpoints, plain JSON, no cryptography.

### Phase 1 — wire it in
Write `config/adapters/providers/pmfby.yaml.tmpl`. Add a `pmfby` entry to
`PROVIDERS` in `bin/setup.py` — two actions on one capability, so it follows
the multi-capability shape with a single entry. Add `PMFBY_*` keys to `.env`
**and to `.env.example`**, since PMFBY has none there today. Compose service
for the mock. Then `make register-provider PROVIDER=pmfby`, then
`make check-mappings` before anything else.

### Phase 2 — prove the network path, no AI involved
Write `capability-examples/Grievance/` payloads — publish, discover, support,
status. They do not exist for any scheme yet, so this work is shared with
PM-KISAN and should be written to serve both.

By hand: `support` returns a ticket; `status` reads it back; a ticket that does
not exist is refused; a record naming a different ticket is refused differently.

### Phase 3 — swap the tools
**Blocked on the OTP decision above.**

`pmfby_grievance_status` can go regardless: new `pmfby_grievance_v2.py` behind
`PMFBY_BECKN_VERSION`, read at import time in `agents/tools/__init__.py`, same
function name, signature and docstring, defaulting to `v1`.

`pmfby_submit_grievance` goes only once the OTP question is answered.

Expect output differences, as with weather — the pack's `comesBack` list is
what survives, and v1 returns whatever the portal sent. Each dropped field gets
a decision recorded here: acceptable loss, or a gap in the pack.

### Phase 4 — write down what is unproven
`quick-try.md`, plus an open-questions section stating that this was proven
against a mock and listing what the real provider could still break.

## Risks

1. **The OTP gate.** Migrating `pmfby_submit_grievance` as the pack currently
   stands is a security regression. This is the top risk and it is a decision,
   not a bug.
2. **We have never reached the real KRPH API.** No base URL in any `.env`, no
   credentials, no observed response. `authScheme: none` is an assumption.
3. **2 of 6 tools.** Easy to overstate what this delivers.
4. **Still a mock.** Everything in "what it does not prove" applies.

## Not in scope

- **PMFBY scheme status** (`initiate_pmfby_status_check`,
  `check_pmfby_status_with_otp`) — needs PMFBY schemas added to
  `SchemeApplication/v0.1` first.
- **Adding `PMFBYInit` to the pack** — the likely answer to the OTP question,
  but a `network-specs` change that should be decided on its own.
- **PM-KISAN** — `../pmkisan/plan.md`.
- **KnowledgeAdvisory**, by decision, goes last.
