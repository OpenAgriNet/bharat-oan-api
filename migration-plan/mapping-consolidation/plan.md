# Consolidating mapping files into network-specs

One source of truth for action mappings, beside the `openapi.yaml` their
`operationRef` already names, served over one domain and referenced from the
registry.

## Where mappings live today

Three places, not two.

| Location | Count | What it is |
|---|---|---|
| `helmcharts/quick-start/config/mappings/<provider>/<usecase>.<action>.yaml` | 32 | the working set -- deployed, newman-verified |
| `network-specs/api/external/<provider>/<usecase>/v0.2/mappings/<action>.yaml` | 48 | mostly drafts: 37 carry `deployable: false` |
| `mapping-files` nginx container | - | serves the helmcharts folder at `http://mapping-files/` |

The registry stores a mapping URL per action, so there is no fourth copy -- the
`ProviderSchema` row points at one of the above. Live split, read from the
registry:

| host | actions |
|---|---|
| `mapping-files` (local nginx -> helmcharts) | 29 |
| `raw.githubusercontent.com` (helmcharts tag) | 4 |

The four on raw URLs are the v0.1 providers: agmarknet, bharat-vistaar,
mausamgram, pocra.

A second nginx, `spec-files`, already serves the whole `network-specs` tree and
already answers both the schema packs and the draft mappings:

```
spec-files:/api/external/pm-kisan/application/v0.2/mappings/init.yaml  200
spec-files:/schema/SchemeApplication/v0.2/attributes.yaml              200
```

So the destination is already reachable. Consolidating retires `mapping-files`
entirely and leaves one file server.

## Which copy wins: helmcharts, for all 32

Read from the live registry: **0 of 33 registered actions point at
network-specs.** Twenty-nine point at the `mapping-files` nginx (which serves
the helmcharts folder) and four at a `raw.githubusercontent.com` helmcharts
tag.

So no mapping file in network-specs has ever served a request. Where the two
copies differ, the network-specs side is not newer -- it is unexercised. The
helmcharts copy is the one newman and the agent have been run against.

**Decision: the helmcharts file wins every time.** This is a bulk promotion
with a path map, not a per-file merge.

Four files carry logic in network-specs that helmcharts does not. It is being
discarded, deliberately, and recorded here so it is recoverable:

| file | what the network-specs copy has extra |
|---|---|
| `pm-kisan/grievance/.../support.yaml` | two lines reading `response.grievanceId` |
| `agmarknet/market-prices/.../select.yaml` | four lines |
| `imd/mausamgram/.../select.yaml` | helmcharts is ahead by 19 -- nothing lost |
| `smam/benefit-status/.../status.yaml` | helmcharts is ahead by 16 -- nothing lost |

The first two are worth a look later as possible improvements; neither has been
tested against a provider. Git history in network-specs keeps them.

## What the drafts are

Of the 48 in network-specs, 37 declare `deployable: false` with
`status: draft-native` / `source-traced` / `incomplete`. Where a tested
helmcharts file exists, the draft is superseded -- `pmfby/insurance.status` is
137 working lines against a 15-line draft. Nineteen drafts have no working
counterpart at all (listed at the end).

## Target structure

network-specs already has the right shape. This records it as the convention
rather than inventing one, and names the rules it implies.

```
api/external/
|-- README.md                      inventory of every documented service
|-- mappings-index.json            authoritative mapping list; validated against disk
`-- <provider>/                    the ORGANISATION: pm-kisan, pmfby, imd, agmarknet, khc
    |-- catalog.json               required iff the provider has an openapi.yaml
    `-- <service>/                 one ENDPOINT GROUP: application, grievance, tickets, loan
        `-- v0.2/                  the OAN pack version these mappings target
            |-- openapi.yaml       verified contract (openapi-draft.yaml while unverified)
            |-- examples/          optional request/response samples
            `-- mappings/
                |-- <action>.yaml          init, confirm, select, status, support, discover
                |-- <action>/<Step>.yaml   only when one action makes several upstream calls
                `-- internal/<name>.yaml   helper calls bound to no Beckn action
```

### The rules this encodes

1. **Provider is the organisation, not the capability.** `imd` holds both
   `mausamgram` and `agromet`; `agmarknet` holds `market-prices` and
   `nearby-prices`.
2. **Service is the endpoint group, not the capability.** The capability code
   lives in `catalog.json` and `mappings-index.json`, where one service can
   carry several and one capability can span providers. This is why
   `agmarknet/market-prices` keeps its name while the index records
   `"capability": "mandi-price"`.
3. **The version is the OAN pack version**, not the provider's own API
   version. A provider moving from v0.1 to v0.2 gets a new folder beside the
   old one, which is what made the SchemeApplication move cheap.
4. **One mapping file per Beckn action, named exactly as the registry names
   the action.** A file called `discover.yaml` served by an action registered
   as `select` is the silent mismatch `pocra/agriculture-facility.select.yaml`
   warns about in its own header.
5. **An action needing several upstream calls gets a folder named for the
   action**, as `aif/loan/v0.2/mappings/init/` already does.
6. **`internal/` is for calls with no Beckn action** -- token exchanges and
   reference lists. KHC's seven lookup lists and GFR's two token helpers are
   the existing examples.
7. **`openapi.yaml` means verified; `openapi-draft.yaml` means source-traced.**
   Promotion is a rename. `aif/loan`, `aif/tickets` and `gfr/advice` sit at
   draft today.
8. **The depth is fixed at provider/service/version.**
   `check-api-separation.py` globs `*/*/*/mappings/**/*.yaml`, so a mapping
   nested any shallower or deeper is invisible to validation and fails the
   index assertion.

### Why the contracts cannot move into one folder

Asked whether every `openapi.yaml` could be collected into a single folder,
holding several contracts side by side. It cannot, and the constraint is in
our own published schema packs.

`schema/SchemeApplication/v0.2/attributes.yaml:532`,
`schema/Grievance/v0.2/attributes.yaml:532` and
`schema/AgricultureResource/v0.2/attributes.yaml:102` all constrain
`operationRef` with:

```
pattern: '^https?://[^#]+/openapi\.yaml#/paths/[^/]+/(get|post|put|patch|delete|head|options|trace)$'
```

The path segment is free (`[^#]+`) but **the filename must literally be
`openapi.yaml`**. That rules out both shapes of the request:

- A single repo-wide folder needs distinct filenames
  (`pm-kisan-application-v0.2.yaml`), which the pattern rejects.
- A per-provider `openapi/` folder cannot hold two files both named
  `openapi.yaml`.

So one folder per contract, each holding a file named `openapi.yaml`, is
forced -- which is what the tree already does. Changing it means amending the
pattern in three published packs, two of which (`Grievance`, `SchemeApplication`)
are now registered at v0.2 and carry live `operationRef` values inside the
packs themselves (`Grievance/v0.2/attributes.yaml:202,375`). Not worth it.

The underlying want -- seeing every contract in one place -- is already served
without moving anything: `api/external/README.md` lists all 18 documented
services with links, and `mappings-index.json` does the same for mappings.
Keeping both current is the fix, not relocating files.

### Inconsistencies to clean up while the files move

| Issue | Fix |
|---|---|
| `index.md` exists only under `pm-kisan`; no other provider has one | delete it -- `README.md` is the inventory |
| Mapping files carry `status:` and `deployable:` headers that duplicate `mappings-index.json` | keep the status in the index only; the index already has a documented legend |
| `README.md` lists `khc/tickets` as source-traced, but it now has a real `openapi.yaml` | regenerate the table after the move |
| helmcharts provider folders do not match the network-specs slugs | resolved by the move table: `pmkisan`→`pm-kisan`, `mausamgram`→`imd/mausamgram`, `knowledge`→`knowledge/search`, `shc`→`shc/reports`, `sathi`→`sathi/stock` |

## Registry wiring

Two env var shapes exist and only one needs thought.

| Provider kind | Variable | Value |
|---|---|---|
| single-capability (mausamgram, agmarknet, vistaar, sathi, shc, pocra) | `<PREFIX>_MAPPING_URL` | full URL of one file |
| multi-capability (pmkisan, pmfby, aif, gfr, smam, kcc, khc) | `<PREFIX>_MAPPING_BASE_URL` | a folder; filenames come from `setup.py`'s `PROVIDERS` table |

Single-capability providers carry a whole URL, so they move with an env edit
and no code change.

Multi-capability providers are the only wrinkle: the base is per *provider*
today, but the new tree splits by *use case* (PMFBY has `grievance/` and
`insurance/`). **The fix needs no code change either** -- point the base at
`.../api/external` and put the rest of the path in the table's mapping field:

```python
("init", "/ChatbotOTP", "pm-kisan/application/v0.2/mappings/init.yaml", 20000, 0),
```

`register_capabilities` already joins base + mapping verbatim
(`setup.py:706-711`), so a longer right-hand side is all it takes. The base
value then becomes identical for all seven providers -- redundant but
backwards-compatible, and collapsing the seven into one variable can follow
later as its own change.

### Which URL the registry carries

Mappings are read from the `network-specs` **`bv-migration`** branch:

```
https://raw.githubusercontent.com/OpenAgriNet/network-specs/bv-migration/api/external/<provider>/<service>/v0.2/mappings/<action>.yaml
```

Chosen over the alternatives for three reasons. GitHub Pages builds from
`main`, so the `openagrinet.github.io` URLs 404 until the merge lands. The
local `spec-files` nginx already serves the same tree and would be faster to
iterate against, but it only exists inside the local stack, so verifying
against it would not test what is actually configured. The raw branch URL is
the one that works both locally and from a deployed adapter today.

It is mutable, unlike the `helmcharts/0.2.0` tag it replaces. That is accepted
for now and tracked: once `bv-migration` merges, these move to a tag or to the
Pages domain, and the whole set changes in one edit because the branch name
appears exactly once per provider variable.

The files must be pushed to `bv-migration` before `setup.py` runs, or every
fetch 404s and `jsonmapper` caches the failure for an hour.

## How the adapter fetches a mapping

`jsonmapper` fetches the URL over HTTP and caches the compiled result.
Defaults, from `pkg/plugin/implementation/jsonmapper/jsonmapper.go`:

| Setting | Default |
|---|---|
| `FetchTimeout` | 5s per fetch |
| `CacheTTL` | 1 hour (failures cached too) |
| `MaxCacheEntries` | 200 |

Two consequences. There is **no domain allow-list** for mappings -- only an
http/https scheme check at `jsonmapper.go:605` -- so unlike schemas, no
`extendedSchema_allowedDomains` edit is needed. And the 5s timeout plus a
negative cache means a slow or 404 host poisons an action for an hour; the
cutover wants `stack.sh check-mappings` green *before* the registry is
repointed, not after.

## Phases

| # | Step | Verification |
|---|---|---|
| 1 | Copy all 32 helmcharts files to their network-specs path, overwriting drafts | every destination exists, content matches the source |
| 2 | Delete superseded drafts and the ones ruled out (`knowledge/.../discover.yaml`, `imd/agromet`, `npss/diagnosis`) | - |
| 3 | Update `mappings-index.json`: add every new path, drop deleted ones, flip promoted entries to `status: existing` | `python3 scripts/check-api-separation.py` passes |
| 4 | Repoint `setup.py`'s `PROVIDERS` mapping values to paths relative to `api/external` | `setup.py --dry-run` diff review |
| 5 | Repoint the env: `*_MAPPING_URL` to full URLs, `*_MAPPING_BASE_URL` to `.../api/external` | `stack.sh check-mappings` -- fetches every registered URL, fails on any 404 |
| 6 | Re-run `setup.py` so the registry rows carry the new URLs | registry survey shows 33 actions on the new host |
| 7 | Full newman suite, then the tool layer, then the agent layer | the three-layer rule |
| 8 | Delete `helmcharts/quick-start/config/mappings/` and retire the `mapping-files` container | suite still green afterwards |

Step 5 must be green before step 6. `jsonmapper` caches failures for an hour,
so repointing the registry at a URL that 404s poisons that action until the
TTL expires.

Step 7 is the real gate: 29 of 33 registered actions change their mapping
source, making this the broadest single change in the migration so far.

### FFS

Out of scope and untouched. One note for step 8: `FFS_MAPPING_BASE_URL` points
at `http://mapping-files/ffs`, a folder that **does not exist** -- all three
FFS bindings resolve to 404 today, verified against the live nginx. FFS works
because it runs the v1 BAP path, not these mappings. Retiring the container
changes nothing for FFS, and `check-mappings` reports those three as failures
before and after.

## Destinations, resolved

All three ambiguous files were settled by matching the mapping's endpoint
against the `openapi.yaml` in each candidate folder.

| file | destination | why |
|---|---|---|
| `agmarknet/mandi-price.select.yaml` | `agmarknet/market-prices/v0.2/mappings/select.yaml` | that folder's openapi declares `/v1/fetch-agmarknet-vistaar`, the exact endpoint the mapping calls. `nearby-prices` declares `/v1/fetch-agmarknet-vistaar-location`, a different call with no registry binding |
| `gfr/soil-report.select.yaml` | `gfr/crop-registry/v0.2/mappings/select.yaml` | that openapi reads "looks up crop registry records by state through the Soil Health GraphQL endpoint" -- it is the soil report call under another name |
| `knowledge/knowledge-advisory.select.yaml` | `knowledge/search/v0.2/mappings/select.yaml` | folder has the openapi; the file is named for the registry's action, `select`, and the draft `discover.yaml` is deleted |

On naming: the folder stays `market-prices` rather than becoming `mandi-price`
because `mappings-index.json` already records that path with
`"capability": "mandi-price"`, the provider `catalog.json` and a `MandiPrice`
schema example both reference the folder by URL, and every other provider is
organised by endpoint group (`pm-kisan/application`, `khc/tickets`) rather
than by capability. The capability name is carried in the index, where it is
already consistent.

## The index is a hard constraint

`network-specs/scripts/check-api-separation.py:19-20` asserts that the mapping
paths listed in `api/external/mappings-index.json` are **exactly** the set of
files on disk:

```python
mapping_paths = {(external/m['path']).resolve() for m in inventory['mappings']}
assert mapping_paths == {p.resolve() for p in external.glob('*/*/*/mappings/**/*.yaml')}
```

Every file added or deleted has to be reflected in that index in the same
commit, or the repo's own validation fails. The index also carries a `status`
per mapping against a documented legend, so promoting a file means flipping
its status to `existing` -- the index becomes the place that records which
mappings are deployable, replacing the `deployable: false` header in the files
themselves.

The same script asserts a provider has a `catalog.json` if and only if it has
an `openapi.yaml`.

## Three services sit on a draft contract

`aif/loan`, `aif/tickets` and `gfr/advice` carry `openapi-draft.yaml` rather
than `openapi.yaml` -- source-traced from the provider's client code, not
verified against the provider. Not a blocker for this change, since the
registry points at the mapping and not at the contract, but `operationRef`
cannot be written for those actions until each draft is verified and renamed.
Recorded as a follow-up.

## A side benefit worth taking

The helmcharts files carry long rationale headers -- 51 comment lines on
`pocra/agriculture-facility.select.yaml`, whose logic is byte-identical to the
network-specs copy. The project convention is that rationale lives in
migration-plan docs, not in modules. Reconciliation is the natural moment to
move those headers here and leave one-line comments in the files.

## Move table

| helmcharts | network-specs | destination state |
|---|---|---|
| `pmkisan/grievance.init.yaml` | `pm-kisan/grievance/v0.2/mappings/init.yaml` | identical |
| `pmkisan/grievance.support.yaml` | `pm-kisan/grievance/v0.2/mappings/support.yaml` | **drifted, ns ahead** |
| `pmkisan/grievance.status.yaml` | `pm-kisan/grievance/v0.2/mappings/status.yaml` | identical |
| `pmkisan/application-status.init.yaml` | `pm-kisan/application/v0.2/mappings/init.yaml` | identical |
| `pmkisan/application-status.status.yaml` | `pm-kisan/application/v0.2/mappings/status.yaml` | identical |
| `pmfby/grievance.init.yaml` | `pmfby/grievance/v0.2/mappings/init.yaml` | new |
| `pmfby/grievance.confirm.yaml` | `pmfby/grievance/v0.2/mappings/confirm.yaml` | new |
| `pmfby/grievance.support.yaml` | `pmfby/grievance/v0.2/mappings/support.yaml` | identical |
| `pmfby/grievance.status.yaml` | `pmfby/grievance/v0.2/mappings/status.yaml` | identical |
| `pmfby/insurance.init.yaml` | `pmfby/insurance/v0.2/mappings/init.yaml` | overwrite draft (96 vs 13) |
| `pmfby/insurance.confirm.yaml` | `pmfby/insurance/v0.2/mappings/confirm.yaml` | overwrite draft (106 vs 13) |
| `pmfby/insurance.status.yaml` | `pmfby/insurance/v0.2/mappings/status.yaml` | overwrite draft (137 vs 15) |
| `pmfby/insurance.select.yaml` | `pmfby/insurance/v0.2/mappings/select.yaml` | overwrite draft (131 vs 16) |
| `aif/loan.init.yaml` | `aif/loan/v0.2/mappings/init.yaml` | new -- note the draft `init/` **folder** beside it |
| `aif/loan.status.yaml` | `aif/loan/v0.2/mappings/status.yaml` | overwrite draft (67 vs 21) |
| `aif/tickets.status.yaml` | `aif/tickets/v0.2/mappings/status.yaml` | overwrite draft (86 vs 26) |
| `kcc/loan.init.yaml` | `kcc/loan/v0.2/mappings/init.yaml` | overwrite draft (122 vs 18) |
| `kcc/loan.status.yaml` | `kcc/loan/v0.2/mappings/status.yaml` | overwrite draft (159 vs 18) |
| `khc/grievance.init.yaml` | `khc/tickets/v0.2/mappings/init.yaml` | overwrite draft (71 vs 25) |
| `khc/grievance.confirm.yaml` | `khc/tickets/v0.2/mappings/confirm.yaml` | overwrite draft (80 vs 25) |
| `khc/grievance.support.yaml` | `khc/tickets/v0.2/mappings/support.yaml` | overwrite draft (124 vs 40) |
| `khc/grievance.select.yaml` | `khc/tickets/v0.2/mappings/select.yaml` | overwrite draft (83 vs 47) |
| `khc/grievance.status.yaml` | `khc/tickets/v0.2/mappings/status.yaml` | overwrite draft (100 vs 69) |
| `smam/benefit.status.yaml` | `smam/benefit-status/v0.2/mappings/status.yaml` | **drifted, helmcharts ahead** |
| `gfr/advice.select.yaml` | `gfr/advice/v0.2/mappings/select.yaml` | overwrite draft (63 vs 32) |
| `gfr/soil-report.select.yaml` | `gfr/crop-registry/v0.2/mappings/select.yaml` | overwrite draft -- **destination unconfirmed** |
| `mausamgram/weather-observation.select.yaml` | `imd/mausamgram/v0.2/mappings/select.yaml` | **drifted, helmcharts ahead** |
| `agmarknet/mandi-price.select.yaml` | `agmarknet/market-prices/v0.2/mappings/select.yaml` | **drifted, ns ahead** -- destination unconfirmed |
| `knowledge/knowledge-advisory.select.yaml` | `knowledge/search/v0.2/mappings/select.yaml` | overwrite draft -- the draft's `discover.yaml` was deleted, not renamed; `VISTAAR_MAPPING_URL` names `select.yaml` |
| `pocra/agriculture-facility.select.yaml` | `pocra/facilities/v0.2/mappings/select.yaml` | same logic, comments differ |
| `sathi/agriculture-resource.select.yaml` | `sathi/stock/v0.2/mappings/select.yaml` | overwrite draft (65 vs 14) |
| `shc/agriculture-resource.select.yaml` | `shc/reports/v0.2/mappings/select.yaml` | overwrite draft (61 vs 13) |

## Orphan drafts (no working counterpart)

| path | status | keep? |
|---|---|---|
| `khc/tickets/v0.2/mappings/internal/{states,districts,sub-districts,villages,schemes,categories,sub-categories}.yaml` | source-traced | keep -- 7 real reference lists |
| `gfr/advice/v0.2/mappings/internal/{generateAccessToken,getTestForAuthUser}.yaml` | source-traced | keep -- token helpers |
| `aif/loan/v0.2/mappings/init/{AIFStartChallenge,AIFVerifyChallenge}.yaml` | source-traced | resolve against the new `init.yaml` first |
| `ffs/booking/v0.2/mappings/*` (5) | draft-native | leave alone -- FFS is out of scope |
| `agmarknet/nearby-prices/v0.2/mappings/select.yaml` | incomplete | delete unless it is the real `mandi-price` home |
| `imd/agromet/v0.2/mappings/select.yaml` | draft-native | delete -- no binding |
| `npss/diagnosis/v0.2/mappings/select.yaml` | incomplete | delete -- no binding |


## What was verified, 2026-11-25

Phases 1 to 7 are done. Phase 8 is deliberately not -- `quick-start/config/mappings/`
and the `mapping-files` container stay in place for a later release.

**Phase 5, every URL resolves.** All 32 moved files return 200 from
`raw.githubusercontent.com/OpenAgriNet/network-specs/bv-migration/api/external/...`,
checked by fetching each one. `setup.py check-mappings` reports `ok` for all 27
registered network-specs URLs. The six it calls `UNREACHABLE` are FFS on
`http://mapping-files/...`, a docker-internal hostname the host cannot resolve
-- expected, and FFS is out of scope either way.

**Phase 6, the adapter actually fetched them.** This is the decisive check, not
the registry survey: `docker logs network-adapter` shows **27 distinct
network-specs URLs fetched and 27 compiled, with no fetch or compile error**.
That is every registered action, agmarknet, imd/mausamgram, pocra and knowledge
included. Nothing resolved from `mapping-files` except FFS.

**Phase 7, three layers.**

| Layer | Result |
|---|---|
| Adapter -- full newman suite | 327 assertions, 2 failures, both the SATHI `Select` that fails because SATHI was never registered ([open issue 3](../open-issues.md)). Every registered capability passed |
| Tools -- 30 direct calls across every v2 provider | 29 returned, 1 raised. The raise was my own fixture calling the **v1** `check_smam_scheme_status` with `phone_number=`; its signature is `(ctx, search_type, search_value)`. Re-run correctly it reaches the network and fails on SMAM having no host -- open issue 3 again |
| Agent -- 45 multi-turn conversations through `agrinet_agent.run` | **20 of the 30 v2 tools reached**, zero raises. A single sentence only ever reaches the first tool of an OTP-gated pair, so these carry the conversation forward with `message_history` and answer the agent's own follow-ups |

Working end to end through the new URLs, with real data returned: PM-KISAN
application and grievance status, PMFBY insurance and grievance across
`init`/`confirm`/`select`/`status`/`support`, AIF loan and tickets, KCC loan,
KHC tickets across all five actions, and Agmarknet mandi prices. MausamGram's
mapping ran -- the adapter logged `served mausamgram|openagrinet:WeatherObservation
in 582 bytes` -- and the upstream had no forecast for the day.

**Nothing that failed was caused by the move.** Each one reproduces with the
old mapping source:

- Mandi, weather and SATHI answering `NO_DATA` is `OAN_PUBLIC_NETWORK_ID`
  defaulting to `*` -- [open issue 29](../open-issues.md).
- SATHI, SHC, GFR and SMAM answering nothing is their missing `.env` block --
  [open issue 3](../open-issues.md).
- PM-KISAN grievance *lodging* answering "temporarily unavailable" is the
  deliberate gate that was already there.
- Agmarknet returning 400 for a same-day forward-dated window is the provider's;
  the "latest available" path returns real prices.

**What the verification itself exposed.** Four newman folders assert only
`HTTP 200` and `not a NACK` and never read the payload, so they pass on an
empty discover -- [open issue 30](../open-issues.md). That is why open issue 29
survived 327 green assertions, and it is the reason the tool and agent layers
are not optional here.


## The agent layer, measured rather than sampled

The first pass asked one sentence per capability and reached 10 of the 30 v2
tools. That is a property of the harness, not the agent: every OTP-gated
journey stops at *"please share the OTP"*, so a one-turn scenario can never
reach the second tool. Three rounds of multi-turn conversations, answering the
agent's own follow-ups, reach **20 of 30**.

| Capability | Through the agent | Through the tool directly |
|---|---|---|
| PMFBY | 6/6 | 6/6 |
| KCC | 3/3 | 3/3 |
| AIF | 3/4 | 4/4 |
| KHC | 2/7 | 7/7 |
| PM-KISAN | 2/5 | 5/5 |
| SATHI | 1/2 | 2/2 |
| SHC, Mandi, Weather | 1/1 each | 1/1 each |

**All 30 work when called directly.** The 10 the agent does not reach are
routing, and each one is an open issue that reproduces on v1:

- **PM-KISAN grievance, 3 tools.** Every grievance phrasing routes to the
  application tools instead — [items 15 and 28](../open-issues.md).
- **KHC filing, 5 tools.** Driven with ordinary farmer sentences the agent made
  **no tool call at all** across five turns, walking the geography questions
  itself and ending on *"Can you please share the name of your village?"*.
  Same family as [item 21](../open-issues.md), which recorded the variant where
  it invents a grievance number instead.
- **`check_aif_loan_status`.** The agent reached `verify_aif_otp` and
  `check_aif_grievance_status` but asked again for the beneficiary id rather
  than reading the loan.
- **`search_sathi_seed_availability`.** SATHI has no `.env` block --
  [item 3](../open-issues.md).

What the agent layer caught that nothing else did: [item 33](../open-issues.md),
`lookup_khc_reference` answering for Bihar when asked for Karnataka, and the
real shape of [item 29](../open-issues.md) below.

### `OAN_PUBLIC_NETWORK_ID` has no correct value

Running the same tools under both settings is what exposed this, and it
reverses the advice this plan carried an hour earlier. The published catalogs
sit in two audiences:

| `visible_to` | catalogs |
|---|---|
| `{*}` | `cat-aif`, `cat-khc`, `cat-sathi` |
| `{oan.dev, net-b.oan.dev}` | `cat-kcc-loan`, both PMFBY, both PM-KISAN, mandi, weather, pocra, knowledge |

The match is literal on both sides -- neither `*` is a wildcard -- so the one
global setting cannot serve both halves. On the default `*`, AIF answers
`Loan application 201: Disbursed` and mandi answers `NO_DATA`. On `oan.dev`,
mandi returns real Pune prices and AIF answers *"The AIF portal is not
available right now"* after logging `AIF v2 discover found no loan catalog`.

**Do not set it to `oan.dev`.** That trades three working capabilities for
four. Leave the default until either `*` is made a wildcard in the visibility
match or the three `{*}` catalogs are republished with the same audience as
the other nine.

## The two file-serving containers are gone

`mapping-files` and `spec-files` were nginx containers bind-mounting a local
directory so the stack could read mappings and provider contracts without the
public site. Both are deleted; `mock-ffs` is deliberately left running.

| Container | Served | Why it could go |
|---|---|---|
| `mapping-files` | `quick-start/config/mappings` | The adapter has fetched every mapping from `raw.githubusercontent.com` since the repoint. Its last real fetch was 07:30:17; after that only probes. Its only remaining consumers were six FFS rows pointing at `/ffs/`, a folder it never contained -- they 404'd from inside the network too. |
| `spec-files` | the whole `network-specs` checkout | One consumer: `PROVIDER_OPENAPI_URL_REWRITE` in `sva_app`. Repointed at the branch. |

The rewrite now reads:

```
PROVIDER_OPENAPI_URL_REWRITE=https://openagrinet.github.io/network-specs/=https://raw.githubusercontent.com/OpenAgriNet/network-specs/bv-migration/
```

A catalog still *names* the published `github.io` URL, which is correct for
everyone reading it; only this deployment redirects it. All fourteen contracts
the catalogs reference load from the branch. When `github.io` publishes v0.2,
delete the line -- nothing else changes.

The adapter never needed `spec-files`: `extendedSchema_contextRewrite` already
pointed `github.io` at the branch, so its `spec-files` allowlist entry was
vestigial and has been removed.

`mock-ffs` stays. FFS is not being migrated to Beckn in this release, and it
reaches its provider through `FFSService` directly, never through the adapter.

### Recreating them

Both images are still local, so this is reversible:

```sh
docker run -d --name spec-files --network oan-internal \
  -v <repo>/network-specs:/usr/share/nginx/html:ro nginx:alpine
docker run -d --name mapping-files --network oan-internal \
  -v <repo>/helmcharts/quick-start/config/mappings:/usr/share/nginx/html:ro nginx:alpine
```

## PR 29 is merged into bv-migration

`feat/gfr-soil-report-and-advice-openapi` targeted `bv-features`; it is merged
into `bv-migration` at `de8603d`. It splits GFR's draft into two final
contracts, `gfr/soil-report/v0.2/openapi.yaml` (`getGfrSoilTest`) and
`gfr/advice/v0.2/openapi.yaml` (`getRecommendations`).

The `operationId` rename only avoids a collision with SHC's
`getTestForAuthUser`. The GraphQL query text is unchanged, so the GFR select
mappings that read `response.data.getTestForAuthUser[0]` keep working, and the
registered mapping URLs are untouched.

`check-api-separation.py` goes from 908 checks to 922, with the same single
pre-existing failure (`Grievance` / `PM-KISAN`, item 26).
