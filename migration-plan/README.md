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
| KHC — grievance registration + status | **In progress** — pack fixed and proven; its status journey contradicts the provider backend, item 20 — [plan](khc/plan.md) · [open items](khc/plan.md#open-items) |
| The other seven Beckn tools | Not started — `advisory`, `agristack`, `amul_vistaar`, `gfr`, `maha_vistaar`, `smam_scheme_status`, `search`. All reach a provider through `BAP_ENDPOINT`, so all are migration targets |
| Six tools that are not migration targets | `imd_advisory` reads its own database, `maps` calls Photon, and `chat_place`, `commodity`, `ffs` and `terms` make no provider call at all. None of them touches Beckn, so there is nothing to move |

Cutting across all of them: the scheme tools used to carry the provider and
resource ids as constants and now ask `discover` for them — what that changed,
how AIF publishes as `AgricultureResource/v0.2`, and why mandi silently
returned no prices until 2026-11-23, is in [discovery.md](discovery.md).

Every capability above ships behind a per-capability flag that defaults to `v1`
in code; the local stack sets all eight to `v2` — see *What the local stack
runs on* below. PM-KISAN is blocked on one thing — a run against the real host. PMFBY is blocked on two values nobody has
given us: its base URL and its login URL.

Four of the open items across the two plans are the same kind of work — gaps
in the published `Grievance` and `SchemeApplication` packs, not in our code.
They should be specified as one `v0.2` pack revision rather than piecemeal.
AIF has now started that revision: both v0.2 packs gained an
`x-oan-schema-by-action` entry pointing `init`/`status`/`support` at a
permissive call schema, because as published they validate a catalog and
reject every operational payload. KCC's work extended it to
`AgricultureResource/v0.2`, the last v0.2 pack without such an entry, which was
refusing every `select` for a missing `informationMode` — a catalog fact a
`select` has no reason to carry. That change is not pushed, and the v0.2 packs
are not published at all — see
[aif/quick-try.md](aif/quick-try.md#before-you-start).

KHC's planning turned up a fact that changes where every v2 catalog should
publish: **the publish module resolves a pinned `@context` too.**
`network-adapter.yaml.tmpl:253` is `oanProviderPublish`, and `:293-295` give it
`extendedSchema_enabled: "true"` with the same `SchemeApplication/v0.2` and
`Grievance/v0.2` rewrite the transaction modules have. A `Grievance/v0.2`
catalog carrying the pinned github.io `@context` published `ACCEPTED` against the
running stack on 2026-11-24. AIF's fallback to `AgricultureResource/v0.2`
predated that, so **AIF's loan resource moved to `SchemeApplication/v0.2`** the
same day and gained `scheme` and a `challenge` naming its six-digit SMS OTP.
Its ticket resource could not follow: `Grievance/v0.2` requires
`grievanceOptions`, AIF publishes no complaint categories and has no filing
call, so it stays where it is — [aif/plan.md](aif/plan.md#open-items) items 6
to 8. The decision procedure — payload type, catalog type, resource count,
decided separately — is in
[khc/plan.md](khc/plan.md#how-we-decide-which-pack-a-capability-goes-under).

The v0.2 packs are reachable again. Production Pages builds from `main`, which
holds v0.1 only, and `bv-migration`'s own preview had been failing to deploy
since 2026-11-23 — one example on an unrelated branch omitted `operationRef`
and the single-process builder raised `NoMethodError`, which took every
preview down with it. Fixed in `network-specs/scripts/generate-schema-pages.rb`
on 2026-11-24; all three v0.2 packs now answer 200 under
`https://openagrinet.github.io/network-specs/previews/bv-migration/`. The
canonical URLs the packs pin still 404 until `bv-migration` merges to `main`,
which is also what retires the `contextRewrite`.

KCC also added vocabulary the packs do not cover and should not: a provider's
own request fields belong in its API contract, not in a schema pack.
`x-oan-request-attributes` names the attributes a Beckn call must carry for a
provider whose wire body is a sealed envelope, and `x-oan-from` names where
each value comes from. Both are read by the tool, not the adapter. See
[kcc/plan.md](kcc/plan.md#what-the-contract-has-to-declare).

Weather's tool and network path are done and verified; IMD Mausamgram itself is
returning no forecast data, so a rendered forecast is the one thing not yet
shown. It is not an outage — the API answers, authenticates and echoes the
coordinates back, but `fcstday1..5` are empty objects. Tracked as open item 4
in [weather/plan.md](weather/plan.md#open-questions); re-run
[weather/quick-try.md](weather/quick-try.md) when IMD is serving again.

## What the local stack runs on

`docker-compose.pilot.yml` now sets every implemented capability to v2. The
flags are read at import time, so changing one means editing that file and
restarting `app`. Checked through `agrinet_agent` with farmer sentences after
the switch:

| Capability | v2 through the agent |
|---|---|
| Mandi, Weather | work (weather's provider still returns an empty forecast) |
| KCC | works — OTP, status, repeated select |
| AIF — loan + grievance | works |
| PM-KISAN — status | works. Its OTP is **four** digits, not six |
| PMFBY — status + grievance | works |
| SATHI | **blocked** — adapter answers `NET_ENTITY_NOT_FOUND`, item 3 below |
| SHC | **blocked** — `discover found no shc-reports resource`, item 3 below |

Nothing regressed in the switch: on v1 every one of these fails locally with
`Connection refused`, because v1 dials the real provider hosts directly and
this stack cannot reach them. SATHI and SHC were already broken and still are,
for the same missing credentials.

## What v2 changes, in one paragraph

v1 sent a single `/search` to a provider the tool had hardcoded in its
environment. v2 splits that into `discover` — which providers can serve this
question, near here — and `select`, the live call to one of them. Both go to
the same consumer-adapter endpoint, which branches on the action in the
payload. Who to call becomes the network's answer rather than an env var --
though the scheme tools only started taking that answer in November 2026; see
[discovery.md](discovery.md).

## Carried to the next phase

Every open item now lives in one place: **[open-issues.md](open-issues.md)**.
Twenty numbered items, grouped by what each one is waiting on — access, a
decision, or an hour of work — plus a link to each capability's own open list.
It is one file so a review does not have to go hunting.

Item 8 is done. `pmkisan_scheme_status.py` kept its own `post_action` and
`_first_resource`, and `pmkisan_grievance.py` imports both *through* that module
while passing `who=` — which the local copy never accepted, so all four of its
calls raised `TypeError` in committed code. Both duplicates are gone and the
shared ones in `aif.py` are used instead. The 266-assertion api collection could
not catch this: newman posts to the adapter and never enters the tool.

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

KCC adds a fourth, about how a capability is signed off:

- **A green api collection does not mean the tool works.** newman posts to the
  network adapter, so 266 passing assertions prove the adapter, the mappings and
  the provider agree with each other. They say nothing about the code the farmer
  actually reaches. PM-KISAN's grievance tool was committed with all four of its
  `post_action` calls raising `TypeError`, and the collection was green the whole
  time. Verify both layers: the newman folder **and** the tool's own entry points,
  happy path and negatives. A stub context is enough —
  `RunContext` only needs `deps=FarmerContext(session_id=..., query=...)`:

      docker exec -i -e PYTHONPATH=/app sva_app python - <<'PY'
      import asyncio, types
      from agents.deps import FarmerContext
      from agents.tools.v2 import kcc
      ctx = types.SimpleNamespace(deps=FarmerContext(session_id="t", query="t"))
      print(asyncio.run(kcc.initiate_kcc_otp(ctx, "9876543211")))
      PY

- **And a green tool does not mean the farmer gets the right answer.** Calling
  the entry points directly supplies the arguments yourself, so it never tests
  whether the agent picks the tool or fills it correctly. Running KCC's three
  tools through `agrinet_agent` with farmer sentences found a fourth layer of
  bug: three of eight phrasings skip `initiate_kcc_otp` entirely and reply "an
  OTP has been sent" — README item 13. Run the agent too, carrying message
  history between turns, with a real `FarmerContext`:

      docker exec -i -e KCC_BECKN_VERSION=v2 -e PYTHONPATH=/app sva_app python - <<'PY'
      import asyncio
      from agents.deps import FarmerContext
      from agents.agrinet import agrinet_agent
      async def main():
          history = []
          deps = FarmerContext(query="", session_id="t", lang_code="en")
          for q in ["check my KCC loan status, my number is 9876543213",
                    "123456", "show me 100247"]:
              deps.query = q
              deps.skills.restore(history)
              res = await agrinet_agent.run(q, deps=deps, message_history=history)
              history = res.all_messages()
              print(q, "->", res.output)
      asyncio.run(main())
      PY

  The flag is read at import time, so the version under test has to be injected
  with `-e`. In the running container `KCC_BECKN_VERSION` is unset — only
  `WEATHER_BECKN_VERSION` and `MANDI_BECKN_VERSION` are `v2` — so the deployed
  agent still routes KCC to the v1 tools.
