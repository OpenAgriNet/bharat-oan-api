# Beckn v1 -> v2 migration

Moving Bharat Vistaar's capabilities from Beckn v1 to v2, one at a time.

```
migration-plan/
├── stack-setup.md     how to bring the whole stack up (do this once)
├── mandi/
│   ├── plan.md        what changed, what it cost, what is still open
│   └── quick-try.md   how to try the mandi use cases
└── weather/
    ├── plan.md        what changed, what it cost, what is still open
    └── quick-try.md   how to try the weather use cases
```

Each capability gets a folder with the same two files.

## Where things stand

| Capability | Status |
|---|---|
| Mandi prices (agmarknet) | **Done** — [plan](mandi/plan.md) · [try it](mandi/quick-try.md) |
| Weather (mausamgram) | **Built, blocked on the provider** — [plan](weather/plan.md) · [try it](weather/quick-try.md) |
| The other sixteen | Not started |

Weather's tool and network path are done and verified; IMD Mausamgram itself is
returning no forecast data, so a rendered forecast is the one thing not yet
shown. Details in [weather/plan.md](weather/plan.md).

## What v2 changes, in one paragraph

v1 sent a single `/search` to a provider the tool had hardcoded in its
environment. v2 splits that into `discover` — which providers can serve this
question, near here — and `select`, the live call to one of them. Both go to
the same consumer-adapter endpoint, which branches on the action in the
payload. Who to call becomes the network's answer rather than an env var.

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
