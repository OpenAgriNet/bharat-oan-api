# Beckn v1 -> v2 migration

Moving Bharat Vistaar's capabilities from Beckn v1 to v2, one at a time.

```
migration-plan/
├── stack-setup.md     how to bring the whole stack up (do this once)
├── discovery.md       how a tool finds out who serves a capability
├── mandi/
│   ├── plan.md        what changed, what it cost, what is still open
│   └── quick-try.md   how to try the mandi use cases
├── weather/
├── pmkisan/
├── pmfby/
├── sathi/
├── shc/
├── aif/
└── kcc/
```

Each capability gets a folder with the same two files. Every `plan.md` that
has unfinished work carries an **`## Open TODOs`** section listing it in one
place -- read that first when picking a capability back up.

## Where things stand

| Capability | Status |
|---|---|
| Mandi prices (agmarknet) | **Done** — [plan](mandi/plan.md) · [try it](mandi/quick-try.md) |
| Weather (mausamgram) | **Built, blocked on the provider** — [plan](weather/plan.md) · [try it](weather/quick-try.md) |
| PM-KISAN — Grievance + SchemeApplication | **Built, proven against a mock only** — [plan](pmkisan/plan.md) · [try it](pmkisan/quick-try.md) · [open TODOs](pmkisan/plan.md#open-todos) |
| PMFBY — Grievance | **Wired up, provider never reached** — [plan](pmfby/plan.md) · [open TODOs](pmfby/plan.md#open-todos) |
| SATHI seed availability | **Built, verified end to end locally** — [plan](sathi/plan.md) · [try it](sathi/quick-try.md) · [open TODOs](sathi/plan.md#open-todos) |
| Soil Health Card | **Built, verified end to end locally** — [plan](shc/plan.md) · [try it](shc/quick-try.md) · [open TODOs](shc/plan.md#open-todos) |
| AIF — loan status + grievance status | **Built, catalog published, discoverable; proven against a mock only** — [plan](aif/plan.md) · [try it](aif/quick-try.md) · [open items](aif/plan.md#open-items) |
| KCC — credit card application status | **Built, catalog published, discoverable; proven against a mock only** — [plan](kcc/plan.md) · [try it](kcc/quick-try.md) · [open items](kcc/plan.md#open-items) |
| The other eleven | Not started |

Cutting across all of them: the scheme tools used to carry the provider and
resource ids as constants and now ask `discover` for them — what that changed,
how AIF publishes as `AgricultureResource/v0.2`, and why mandi silently
returned no prices until 2026-11-23, is in [discovery.md](discovery.md).

Both scheme capabilities ship behind a per-capability flag defaulting to `v1`,
so nothing above is serving farmers on v2 yet. PM-KISAN is blocked on one
thing — a run against the real host. PMFBY is blocked on two values nobody has
given us: its base URL and its login URL.

Four of the open items across the two plans are the same kind of work — gaps
in the published `Grievance` and `SchemeApplication` packs, not in our code.
They should be specified as one `v0.2` pack revision rather than piecemeal.
AIF has now started that revision: both v0.2 packs gained an
`x-oan-schema-by-action` entry pointing `init`/`status`/`support` at a
permissive call schema, because as published they validate a catalog and
reject every operational payload. That change is not pushed, and the v0.2
packs are not published at all — see
[aif/quick-try.md](aif/quick-try.md#before-you-start).

Weather's tool and network path are done and verified; IMD Mausamgram itself is
returning no forecast data, so a rendered forecast is the one thing not yet
shown. It is not an outage — the API answers, authenticates and echoes the
coordinates back, but `fcstday1..5` are empty objects. Tracked as open item 4
in [weather/plan.md](weather/plan.md#open-questions); re-run
[weather/quick-try.md](weather/quick-try.md) when IMD is serving again.

## What v2 changes, in one paragraph

v1 sent a single `/search` to a provider the tool had hardcoded in its
environment. v2 splits that into `discover` — which providers can serve this
question, near here — and `select`, the live call to one of them. Both go to
the same consumer-adapter endpoint, which branches on the action in the
payload. Who to call becomes the network's answer rather than an env var --
though the scheme tools only started taking that answer in November 2026; see
[discovery.md](discovery.md).

## Read this before starting the next one

Two findings from mandi that change how the next capability should be
estimated:

- **The tool did not get smaller.** The original plan predicted a 60%
  reduction; it was 4%. Pagination went away, but new work arrived.
- **Work moved into the AI tool, not out of it.** Because `discover` returns an
  unranked list, choosing the provider and ranking by distance now happen in
  the tool — jobs the v1 provider did. Worth fixing network-side before
  seventeen tools each grow their own copy.

Both are in [mandi/plan.md](mandi/plan.md) with the measurements.

Weather adds a third, and it is the one that will bite every remaining
capability:

- **`networkId` on a discover picks the catalog audience, not the caller.**
  `visible_to` is matched by array overlap, so a public catalog — one published
  `{*}` — is found by asking for `"*"`, and a scoped one by naming its network.
  Each tool has to send the audience its providers publish to; weather sends
  `"*"` because IMD forecast data is open. A capability that needs both at once
  wants the spec's array form of `networkId`, which the adapter currently
  refuses — [weather/plan.md](weather/plan.md#scoping-a-discover-to-a-catalog-audience).
