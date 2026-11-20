# PMFBY: v1 -> v2, against a mock provider

PMFBY (Pradhan Mantri Fasal Bima Yojana — crop insurance) shares the
`Grievance` plugin and pack with PM-KISAN, which is migrated and documented in
`../pmkisan/plan.md`. PMFBY follows it rather than preceding it, as an earlier
draft of this document proposed: that draft argued PMFBY should go first
because its grievance path has no OTP and no encryption, and the OTP half of
that is wrong — see the OTP section below.

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

## The OTP gate — decided 2026-11-20

**Decision: keep what v1 does today, and carry the remaining gap as a TODO.**
Both grievance tools move. The OTP check stays exactly where it already is.

### A correction to what this section said first

I wrote that migrating `pmfby_submit_grievance` means *"anyone who knows a
farmer's mobile number, application number, year and season can file a
grievance in their name"*, where today they would also need an OTP. The first
half is true of v2. The second half is not true of v1, and it changes the
decision.

**v1 never sends the OTP to PMFBY.** `PMfbyGrievanceInitRequest`
(`agents/tools/pmfby_grievance.py:434`) has no OTP field, and neither does the
`/init` body its `get_payload` builds — the tags are request_type, phone,
complaint_date, receipt_source_id, the two category ids, year, season,
application_no, description. What `pmfby_submit_grievance`
(`agents/tools/pmfby_grievance.py:785`) does is call
`_verify_pmfby_otp_with_bap` first — a *separate* `/status` round-trip — and
return early if it comes back unverified.

The gate is a check our own tool runs before it files. PMFBY does not enforce
it, on either version. So `grievance.support.yaml`'s *"Files a grievance; no
OTP"* is not describing a v2 regression. It describes what v1 sends too.

### What the decision means in practice

The v2 tool keeps the gate by doing what the v1 tool does: verify, then file.
`_verify_pmfby_otp_with_bap` is a v1 `/status` call and stays on v1 — it has
nowhere else to go, since the pack has no `PMFBYInit`. The two OTP tools the
farmer actually talks to stay on v1 for the same reason. Only the filing call
itself becomes v2 `support`.

That means the v2 path is **no weaker than the v1 path**, which is the bar the
decision sets. It does not mean the gate is strong. Anyone posting straight at
the adapter skips it — but they skip the v1 BAP `/init` the same way. The
weakness is the status quo, not something this migration introduces.

The mapping needs nothing new from us for this. `grievance.support.yaml`
defaults `ticketCategoryID` to `3`, `ticketSubCategoryID` to `10` and
`receiptSourceID` to `134306`, which are the same three constants v1 hardcodes
at `agents/tools/pmfby_grievance.py:33-35`, and it derives `complaintDate`
itself. Every field v1 sends has a home.

### TODO — move the gate off the client and into the network

The honest version of this is a `PMFBYInit` in the `Grievance` pack: `init`
issues a `challenge`, `support` carries `challenge.response`, and the provider
adapter verifies before it calls KRPH. That is how PM-KISAN's v2 grievance path
works today, and it is the only version where the OTP protects the API rather
than protecting our agent's conversation flow.

Doing it would also give `initiate_pmfby_grievance_otp` and
`check_pmfby_grievance_otp` a v2 target, taking this migration from 2 of 6
tools to 4 of 6. It is a `network-specs` addition, so it lands with the other
pack work — see the `v0.2` items in `../pmkisan/plan.md`.

Until then, two things must stay true, and both belong in the v2 tool's tests:

- `pmfby_submit_grievance` calls `_verify_pmfby_otp_with_bap` before it builds
  the `support` body, and returns the failure message unchanged when it fails.
- The OTP argument keeps its position and validation. A v2 signature that
  quietly makes `otp` optional is the regression I was worried about, arriving
  by a different route.

Note that `pmfby_grievance_status` has none of this. Its docstring says *"No
OTP is required for status lookup"* and v2 agrees. That one is a clean
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
Unblocked: the OTP decision above is made.

`agents/tools/v2/pmfby_grievance.py`, behind `PMFBY_BECKN_VERSION` via
`_beckn_v2("PMFBY")` in `agents/tools/__init__.py`, defaulting to `v1`. Same
function names, signatures and docstrings as the v1 twins. Both tools move:

- `pmfby_grievance_status` — `/search` becomes `status`. Like-for-like.
- `pmfby_submit_grievance` — `/init` becomes `support`, with
  `_verify_pmfby_otp_with_bap` imported from the v1 module and called first,
  unchanged. Two tests pin that: one that a bad OTP never reaches the adapter,
  one that `otp` is still a required first argument.

`initiate_pmfby_grievance_otp` and `check_pmfby_grievance_otp` stay imported
from the v1 module on both flag settings. They have no v2 target and the v2
filing path depends on them.

Expect output differences, as with weather — the pack's `comesBack` list is
what survives, and v1 returns whatever the portal sent. Each dropped field gets
a decision recorded here: acceptable loss, or a gap in the pack.

### Phase 4 — write down what is unproven
`quick-try.md`, plus an open-questions section stating that this was proven
against a mock and listing what the real provider could still break.

## Risks

1. **The OTP gate stays client-side.** Decided, not a regression — v1 does
   not send the OTP downstream either — but it means the v2 path inherits a
   weakness rather than fixing one. The two tests named in Phase 3 are what
   stop it eroding; without them a later refactor can drop the gate and
   nothing fails.
2. **We have never reached the real KRPH API.** No base URL in any `.env`, no
   credentials, no observed response. `authScheme: none` is an assumption.
3. **2 of 6 tools.** Easy to overstate what this delivers.
4. **Still a mock.** Everything in "what it does not prove" applies.

## Not in scope

- **PMFBY scheme status** (`initiate_pmfby_status_check`,
  `check_pmfby_status_with_otp`) — needs PMFBY schemas added to
  `SchemeApplication/v0.1` first.
- **Adding `PMFBYInit` to the pack** — the TODO recorded under the OTP
  decision. A `network-specs` change, batched with the other `v0.2` pack work.
- **PM-KISAN** — `../pmkisan/plan.md`.
- **KnowledgeAdvisory**, by decision, goes last.
