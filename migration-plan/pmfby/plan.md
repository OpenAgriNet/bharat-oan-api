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
| Helm chart adapter config | **done**, on `feat/137-per-environment-config` — `charts/adapter-service/config/single-adapter/network-adapter-config.yaml:337` binds `pmfby\|openagrinet:Grievance` |
| Secret slots | **done** — `scripts/secrets.sh:174` defines `provider-pmfby` with `PMFBY_ACCESS_UID` and `PMFBY_ACCESS_PWD`, and `:162` a `pmfbyTokenUrl` |
| API collection | **done** — the same branch ships publish / discover / support / status requests for PMFBY |
| quick-start adapter template | **missing** — no `pmfby` in `quick-start/config/adapters/single-adapter/network-adapter.yaml.tmpl` on either branch |
| quick-start provider template | **missing** — no `config/adapters/providers/pmfby.yaml.tmpl` |
| `setup.py` `PROVIDERS` entry | **missing** — only `pmkisan` exists |
| quick-start env keys | **missing** — no `PMFBY_*` in `.env.example` |
| Real base URL and login URL | **missing from the repo** — the only committed values are `https://example.invalid` in `charts/registry-seed/ci/lint-values.yaml`. They live in the deployment's own values. |
| `capability-examples/Grievance` | done for PM-KISAN; no PMFBY variants |

The upstream, from the mapping headers, is a PMFBY KRPH API:

| Action | Path |
|---|---|
| `support` | `POST /krphapi/FGMS/AddKRPHNCIPGrievenceSupportTicket` |
| `status` | `POST /krphapi/FGMS/GetGrievenceTicketsStatus` |

"Grievence" is PMFBY's spelling, not a typo in our code, and the mappings say so.

### The provider config, which is already written

An earlier draft of this document guessed that PMFBY would need
`authScheme: none`, since its mappings carry no token or header. That guess was
wrong, and the deployed config says so. From
`charts/adapter-service/config/single-adapter/network-adapter-config.yaml:344`:

```yaml
pmfby:
  authScheme: tokenHeader
  tokenUrl: "__PMFBY_TOKEN_URL__"
  tokenUserField: appAccessUID
  tokenUserEnv: PMFBY_ACCESS_UID
  tokenSecretField: appAccessPWD
  tokenSecretEnv: PMFBY_ACCESS_PWD
  tokenResponseField: responseDynamic.token.Token
  tokenExpiryField: responseDynamic.token.validTo
  headerName: Authorization
```

So the adapter logs in at a PMFBY endpoint with a uid and password, reads a
token out of the reply, caches it until the reply's own `validTo`, and sends it
bare in `Authorization`. No envelope, no OTP, no follow-up read — PMFBY is
still much simpler than PM-KISAN, just not credential-free.

The work left is porting this block into the quick-start stack, which the same
branch did not touch. That is the whole reason a PMFBY call at the local
adapter returns `NET_ENTITY_NOT_FOUND` today.

## Why a mock is no longer the plan

The earlier draft built a mock because we had no credentials and no base URL.
Both now exist in the deployment. Running against the real staging service is
the house rule, so Phase 0 becomes "get the two URLs and the two credentials
into the local stack" rather than "write a stand-in".

A mock stays worth having later, for CI, where reaching a real service on every
run is not acceptable. It is not a prerequisite for this migration any more.

The swap to the real service later is a registry write
(`make register-provider PROVIDER=pmfby UPDATE=1`), not a code change, because
the upstream address lives in the `ProviderSchema` row.

**What a mock would prove here:** routing, binding keys, schema validation
against the real published `PMFBYSupport` and `PMFBYStatus`, both mappings, and
the v2 tool wiring end to end.

**What it does not prove:** anything about the real KRPH API — its field
spellings, error bodies, no-data behaviour, auth, or rate limits. PMFBY has far
less of our guesswork in it than PM-KISAN does (no invented OTP window, no
assumed cipher parameters), so the gap is narrower, but it is the same gap.

**This cannot be signed off as "PMFBY works"** — only as "the v2 path works,
pending the real provider."

## The mock — deferred, for CI only

Not a prerequisite any more, now that the real service is reachable. Kept here
because CI cannot call PMFBY on every run. Build it after the real path is
proven, so it can be made to lie in the ways the real service actually lies.

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

### Phase 0 — collect four values
From whoever deployed `feat/137-per-environment-config`: the PMFBY **base URL**
and **login URL**, plus `PMFBY_ACCESS_UID` and `PMFBY_ACCESS_PWD`. Staging if
there is one. None of the four is in the repo; the committed values are
`https://example.invalid`.

Credentials go in `.env`, which is gitignored, and nowhere else. If either
credential has been pasted into a chat, a ticket, or a commit, ask PMFBY to
rotate it before it is used — see the note under Risks.

### Phase 1 — wire it into quick-start — DONE 2026-11-20

Done in `helmcharts` on `bv-migration`:
`config/adapters/providers/pmfby.yaml.tmpl` (the `pmfby` auth block, ported
verbatim from the deployed chart), a `pmfby` entry in `PROVIDERS` in
`bin/setup.py`, the `PMFBY_*` placeholders in `.env.example`, and
`PMFBY_ACCESS_UID` / `PMFBY_ACCESS_PWD` passed into both adapter services in
`docker-compose.yml`.

PM-KISAN and PMFBY answer the same capability, so they share ONE `Grievance`
step -- the adapter picks per-provider config by binding key from inside a
step, and a second step with the same id would shadow the first. Provider
templates therefore split into a head and a body on a `__PROVIDER_CONFIG__
<StepId>` marker: one template carries the step header, the rest name the step
they join. `setup.py` groups by step rather than by provider.

This is a port, not a design. The deployed config is the source of truth and
anything that disagrees with it is a bug here.

#### What the port was verified against

| Check | Result |
| --- | --- |
| `make register-provider PROVIDER=pmfby` with placeholders still in `.env` | refuses, names all four keys |
| Rendered `network-adapter.yaml` | one `Grievance` step, `bindingKeys` = `pmfby\|openagrinet:Grievance,pmkisan\|openagrinet:Grievance,pmkisan\|openagrinet:SchemeApplication` -- same as the deployed chart |
| Re-render with pmfby absent | byte-identical to the previous config apart from one rewritten comment, so the grouping change is a no-op for existing providers |
| `support` probe | passes schema, resolves `pmfby\|openagrinet:Grievance`, fetches the mapping, then `NET_DOWNSTREAM_UNAVAILABLE` "after 1 attempt" at the token exchange |
| `status` probe | same, "after 3 attempts" -- `retryMax: 0` and `retryMax: 2` are both live |
| The five shipped `capability-examples` PM-KISAN fixtures | all five still answer `on_init` / `on_support` / `on_status` from the real PM-KISAN service |

The probes ran with `PMFBY_BASE_URL` and `PMFBY_TOKEN_URL` set to a host that
does not resolve, on purpose. Everything we own is proven -- schema, routing,
mapping fetch, token-header auth wiring. Nothing downstream of the socket is:
PMFBY has still never answered us.

#### The mapping tag was wrong, and is fixed

The first probe failed with a 404 on the mapping fetch:
`PMFBY_MAPPING_BASE_URL` in `.env.example` was tag `0.1.0`, copied from the
`PMKISAN_MAPPING_BASE_URL` line above it, and neither provider's mappings
exist at that tag. Tag `0.2.0` carries all seven -- five pmkisan, two pmfby --
and each one is byte-identical to the working copy in
`quick-start/config/mappings/`. Both lines now point there, in `.env.example`
and in `.env`.

### Phase 2 — prove the network path, no AI involved
The same branch's `api-collection/OpenAgriNet.api-collection.json` already
ships PMFBY publish, discover, support and status requests under
`Grievance / PMFBY`. Replay those against the local adapter rather than writing
new ones; they carry the field values PMFBY's own team used.

By hand: `support` returns a ticket; `status` reads it back by `case.ticketNo`;
a ticket that does not exist is refused; a wrong mobile is refused differently.
A refusal only counts if the code starts with `BIZ_`.

Note that PMFBY `status` keys off `case.ticketNo` and needs no filing date.
PM-KISAN's `case.filedOn` problem does not arise here.

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

## Open TODOs

Everything that is blocked or deferred, in one place. Nothing here is being
worked on now.

### Blocking the end-to-end run

1. **The real PMFBY base URL and login URL.** Not in either repo — the only
   committed value is `https://example.invalid` in
   `charts/registry-seed/ci/lint-values.yaml`. Ask whoever deployed
   `feat/137-per-environment-config` for the registry-seed values file.
   Staging if there is one. Until these arrive
   `make register-provider PROVIDER=pmfby` refuses, by design, and the local
   registry holds an unreachable placeholder host.
2. **Reissue `PMFBY_ACCESS_UID` / `PMFBY_ACCESS_PWD`.** The password was
   pasted in plain text into a chat thread on 2026-11-20. Ask PMFBY for a new
   pair before the credentials are used for anything that writes — filing a
   grievance writes. See risk 3.
### Blocked on the above

3. **Phase 2 — replay the branch's API collection** against the local adapter.
   Needs item 1. Nothing about PMFBY's actual responses is known until this runs,
   so every field name in both mappings is still unverified — see risk 2.
4. **Phase 3 — write `agents/tools/v2/pmfby_grievance.py`.** Not technically
   blocked, but writing it before item 3 means writing against guessed
   response shapes.

### Deferred by decision

5. **Push the OTP challenge into the pack** — a `PMFBYInit` in `Grievance`, so
   the gate protects the API rather than the agent's conversation flow. Lands
   with the `v0.2` pack work. Detail under the OTP section above. Takes this
   migration from 2 of 6 tools to 4 of 6.
6. **The mock provider** — CI only, written after the real API has answered,
   not before. See "The mock — deferred, for CI only".
7. **Keep the chart and quick-start in step.** Both now carry PMFBY, but
   nothing fails when they drift. See risk 5.

## Risks

1. **The OTP gate stays client-side.** Decided, not a regression — v1 does
   not send the OTP downstream either — but it means the v2 path inherits a
   weakness rather than fixing one. The two tests named in Phase 3 are what
   stop it eroding; without them a later refactor can drop the gate and
   nothing fails.
2. **We have still never reached the real KRPH API.** The config, the auth
   scheme and the credentials now exist; a response from PMFBY does not. Every
   field name in both mappings is read off their documentation, not off
   something observed. `responseDynamic.token.Token` and the `responseCode`
   `"1"` success test are the two most likely to be wrong.
3. **`PMFBY_ACCESS_PWD` has been shared in plain text.** It was pasted into a
   chat thread on 2026-11-20 while handing over the deployment details. Chat
   history is not a secret store and is usually retained and searchable. Ask
   PMFBY to reissue the pair before the staging credentials are used for
   anything that writes — filing a grievance writes. Until then it belongs in
   `.env` only, never in a values file, a commit, or this document.
4. **2 of 6 tools.** Easy to overstate what this delivers.
5. **The chart and quick-start can drift.** Both carry PMFBY now, but the
   quick-start copy was ported by hand from
   `charts/adapter-service/config/single-adapter/network-adapter-config.yaml`
   and nothing checks that they still agree. Whichever is changed next, the
   other has to follow.

## Not in scope

- **PMFBY scheme status** (`initiate_pmfby_status_check`,
  `check_pmfby_status_with_otp`) — needs PMFBY schemas added to
  `SchemeApplication/v0.1` first.
- **Adding `PMFBYInit` to the pack** — the TODO recorded under the OTP
  decision. A `network-specs` change, batched with the other `v0.2` pack work.
- **PM-KISAN** — `../pmkisan/plan.md`.
- **KnowledgeAdvisory**, by decision, goes last.
