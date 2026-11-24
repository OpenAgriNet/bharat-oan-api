# PM-KISAN and PMFBY grievance: `Grievance/v0.1` -> `v0.2`

**Status: proposed, nothing built.** Written 2026-11-24 for review.

KHC is on `Grievance/v0.2`. PM-KISAN and PMFBY grievance are still on `v0.1`,
and three of the migration's open items exist only because of that. This moves
both, together, because they share one context constant and doing either alone
costs nearly the same as doing both.

- Open items this closes: [../open-issues.md](../open-issues.md)
- The capability that proved v0.2: [../khc/plan.md](../khc/plan.md)

---

## Why v0.1 is the cause, not a coincidence

`Grievance/v0.1` validates each provider with its own closed schema. Three
consequences, all measured:

| v0.1 | Where | What it forces |
|---|---|---|
| `PMKISANOption.code` is a closed enum, `[G001…G010]` | `v0.1/attributes.yaml:841` | thirteen of PM-KISAN's real grievance types, `CORRUPTION` among them, cannot be published. The tool carries a refusal branch instead |
| `PMKISANStatus` requires `case`, and `mustSend` lists `case.filedOn` | `v0.1/attributes.yaml:645` | the farmer must produce a filing date they were shown once. A wrong date is indistinguishable from having no grievance |
| There is no `PMFBYInit` — `v0.1` has `PMKISANInit` and no PMFBY equivalent | `v0.1/attributes.yaml` schema list | PMFBY's grievance OTP has no action to ride, so it stays on the v1 BAP |

`Grievance/v0.2` maps all five actions to one `GrievanceCall`:

```yaml
x-oan-schema-by-action:
  init: GrievanceCall
  confirm: GrievanceCall
  select: GrievanceCall
  status: GrievanceCall
  support: GrievanceCall
```

`GrievanceCall` requires `@context` and `@type` and nothing else. Per-operation
validation moves to the provider's OpenAPI contract, which the catalog's
`actions[].operationRef` names. So the enum, the required `case` and the missing
`PMFBYInit` all stop being pack problems at once — not because v0.2 relaxed a
rule for us, but because v0.2 puts provider-specific shapes where they belong.

## The last v1 call, and why it is here

Filing a PMFBY grievance on v2 still reaches the old v1 BAP.
`agents/tools/v2/pmfby_grievance.py:40` imports `_verify_pmfby_otp_with_bap`
from the **v1** module and `:349` calls it, which posts to `BAP_ENDPOINT/status`
(`agents/tools/pmfby_grievance.py:140, 410`). With `BAP_ENDPOINT` unset it
raises. So PMFBY cannot be cut over to v2 on its own, and this is the only
capability where that is still true.

It is here rather than in PMFBY's own plan because the fix is the pack move:
once the grievance capability has an `init` and a `confirm`, the OTP has
somewhere to go.

## What the move actually costs

Less than it looks, because the endpoints are already mapped — under the other
capability.

PMFBY's two OTP endpoints are on the same host as its grievance endpoints
(`PMFBY_BASE_URL`), and `SchemeApplication/v0.2` already registers both:

| Action | Path | Existing mapping |
|---|---|---|
| `init` | `/api/v1/services/nic/getOtp` | `insurance.init.yaml` |
| `confirm` | `/api/v1/services/nic/verifyMobile` | `insurance.confirm.yaml` |

So `grievance.init.yaml` and `grievance.confirm.yaml` are near-copies of two
proven, working mappings, not new work against an unknown API. PM-KISAN needs no
new mapping at all: its three actions (`init`, `support`, `status`) keep their
paths and are repointed at v0.2.

One contract decision: `getOtp` and `verifyMobile` are declared in
`api/external/pmfby/insurance/v0.2/openapi.yaml`, not the grievance one. Either
point grievance's `init`/`confirm` `operationRef`s into the insurance contract,
or declare the two operations in both. Cross-referencing is honest — it is one
host and one verification — and avoids two copies drifting.

Worth noting: `api/external/pmfby/grievance/v0.2/openapi.yaml` **already** names
`Grievance/v0.2` in its `x-oan-binding.schema`, while `setup.py` registers the
capability at `v0.1`. The contract was written for the move; only the
registration and the catalog were left behind.

## The one thing that is real work: PMFBY has no categories

`Grievance/v0.2`'s `GrievanceFields` requires `grievanceOptions`
(`v0.2/attributes.yaml:382-390`). PM-KISAN has a category list. **PMFBY does
not**, and the current behaviour is worse than missing:

- KRPH's filing operation takes `ticketCategoryID` and `ticketSubCategoryID`
  (`pmfby/grievance/v0.2/openapi.yaml:12`).
- `grievance.support.yaml:9-11` hardcodes `$DEFAULT_CATEGORY_CODE := "3"`,
  `$DEFAULT_SUB_CATEGORY_CODE := "10"`, `$DEFAULT_RECEIPT_SOURCE_ID := "134306"`.
- `pmfby_submit_grievance` takes no category argument on either version — only
  `application_no` and a free-text `grievance_description`.

So **every PMFBY grievance we file is filed under category 3, sub-category 10**,
whatever the farmer complained about, and the farmer is never asked. The desk
that receives a grievance is chosen by that code. This is the same routing
failure `../pmkisan/plan.md` rejects as Option B, except it is already live and
nobody chose it.

Requiring `grievanceOptions` forces this into the open, which is the strongest
argument for the move. It also means the move cannot complete without PMFBY's
real category list. **That is a question for KRPH, not a lookup**, and it is the
one dependency here that is not ours to close.

Until it arrives, the choice is: publish the two codes we actually send as a
one-entry `grievanceOptions` and say in the catalog that it is provisional, or
hold PMFBY at v0.1 and move PM-KISAN alone. The first is honest about what we
do; the second leaves the v1 BAP call in place.

## Plan

Phases are ordered so each one is verifiable before the next starts.

### Phase 0 — ask, before building

1. Ask KRPH for PMFBY's `ticketCategoryID` / `ticketSubCategoryID` list, and
   what `134306` as a `receiptSourceID` means.
2. Ask PM-KISAN whether its grievance status read can answer without a filing
   date, and how long its OTP is valid (open item 4, which this does **not**
   close).

Neither blocks phases 1-3.

### Phase 1 — PM-KISAN to v0.2

1. `GRIEVANCE_CONTEXT` at `agents/tools/v2/pmkisan_scheme_status.py:53` to the
   v0.2 context URL. One constant, imported by `v2/pmfby_grievance.py:45`, so
   this is the line that moves both.
2. `setup.py`'s `pmkisan` Grievance capability from `v0.1` to `v0.2`.
3. `@context` in `grievance.init.yaml`, `grievance.support.yaml`,
   `grievance.status.yaml`.
4. Edit the catalog in the api collection — **Grievance / PM-KISAN /
   Publish**, `catalogId` `catalog:grievance:pmkisan` — to the v0.2 `@context`,
   publishing the **full** category list, the ten plus the thirteen, collapsed
   as `../pmkisan/plan.md` argues (four or five headings, not thirteen). The
   collection is this catalog's only publisher: nothing under
   `capability-examples/Grievance/publish/` covers PM-KISAN, and no make target
   publishes automatically.
5. Delete `_CATEGORY_CODES` and its refusal branch in `v2/pmkisan_grievance.py`.
6. Drop the pack-side requirement on `filed_on`: `grievance.status.yaml`'s
   `$filter(... = $ca.case.filedOn)[0]` returns the list unfiltered when no
   `case` is supplied, and `filed_on` becomes optional on the tool.

**Verify:** api collection PM-KISAN folder green; then the agent, with a farmer
sentence that names no date, and one that names a category outside G001-G010.

### Phase 2 — PMFBY to v0.2

1. `setup.py`'s `pmfby` Grievance capability from `v0.1` to `v0.2`, plus two
   actions: `init` -> `/api/v1/services/nic/getOtp` and `confirm` ->
   `/api/v1/services/nic/verifyMobile`, both never retried — a retry sends or
   spends a second OTP.
2. `grievance.init.yaml` and `grievance.confirm.yaml`, copied from
   `insurance.init.yaml` / `insurance.confirm.yaml`.
3. Point the catalog's `init`/`confirm` `operationRef`s into the insurance
   contract.
4. The same edit to **Grievance / PMFBY / Publish**, `catalogId`
   `cat-pmfby-grievance`, with whatever phase 0 returns for `grievanceOptions`
   — or a provisional one-entry list, labelled as such.
5. Rewrite `initiate_pmfby_grievance_otp` and `check_pmfby_grievance_otp` as v2
   tools in `agents/tools/v2/pmfby_grievance.py`, keeping v1's names,
   signatures and docstrings.
6. Move their imports in `agents/tools/__init__.py:74` inside the
   `if _beckn_v2("PMFBY")` branch, and drop the `_verify_pmfby_otp_with_bap`
   import at `v2/pmfby_grievance.py:40`.

**Verify:** `BAP_ENDPOINT` unset, then file a grievance end to end against
`mock-pmfby`. That is the test — if it passes with no BAP configured, the last
v1 dependency is gone. Then the api collection, then the agent.

### Phase 3 — record

Close PM-KISAN items 2, 3 and 5 and PMFBY item 5 in
[../open-issues.md](../open-issues.md); correct PMFBY's stale `2 of 6` count and
Open TODO 6 (`SchemeApplication/v0.2` exists, those two tools are live); note
the `deployable: false` drift between `network-specs/api/external/pmfby/
insurance/v0.2/mappings/` and the working copies in `quick-start`.

## What this does not close

Said plainly, so the move is not oversold:

| Still open | Why this does not touch it |
|---|---|
| PM-KISAN's invented 10-minute OTP window (`grievance.init.yaml:28`) | a fact nobody has. Phase 0 asks |
| PMFBY and PM-KISAN never reached their real providers | no credentials. Not ours |
| The four prompt-fabrication bugs (items 13, 15, 16, 21) | identical on v1, parked by decision |
| Pages builds from `main`, so every canonical `v0.2` URL 404s | a merge, not code — and the bigger deployment risk than this move |
| Re-enabling PM-KISAN's grievance lodge path | **unblocked** by phase 1, but still someone's decision. It writes to a government system |
| PM-KISAN application status is on `SchemeApplication/v0.1` (`v2/pmkisan_scheme_status.py:48`) while KCC, AIF and PMFBY insurance are on v0.2 | adjacent and the same shape, but a different pack and a different catalog (`catalog:scheme-application:pmkisan`). Deliberately out of scope — doing four catalogs in one change makes a failure unattributable |

## Risks

1. **PMFBY's categories may not arrive.** Then phase 2 ships a provisional
   `grievanceOptions`, and we are publishing a list we know is incomplete.
   Better than hardcoding `3` silently, worse than the truth. Give it a dated
   end.
2. **One constant moves two capabilities.** `GRIEVANCE_CONTEXT` is shared, so a
   mistake in phase 1 breaks PMFBY grievance too. Phase 1's verification has to
   cover both before phase 2 starts.
3. **`GrievanceCall` validates almost nothing.** Moving to v0.2 trades
   pack-enforced shapes for contract-enforced ones, and the contract is only as
   good as the OpenAPI. PMFBY's grievance contract is written but was never
   checked against the real KRPH API — see risk 2 in `../pmfby/plan.md`.
4. **Changing `visibleTo` at the same time would be a second bug wearing the
   first one's clothes.** Both grievance catalogs publish `visibleTo:
   ["{{networkId}}", "{{otherNetworkId}}"]` and both tools discover with
   `OAN_NETWORK_ID`, default `oan.dev` (`v2/pmkisan_scheme_status.py:95`), so
   the pair agrees. KHC and AIF use the other self-consistent pair, `["*"]`
   against `"*"`, and **`*` is matched literally, not as a wildcard** — mixing
   them is what broke `check_khc_grievance_status` on 2026-11-24
   ([../khc/plan.md](../khc/plan.md), defect 4). Pack version and visibility are
   independent. Move one per change, or a failure cannot be attributed.
