# PM-KISAN and PMFBY grievance: `Grievance/v0.1` -> `v0.2`

**Status: proposed, nothing built.** Written 2026-11-24, corrected 2026-11-25
after tracing the publish -> discover -> call -> mapper chain against the
running stack.

KHC is on `Grievance/v0.2`. PM-KISAN and PMFBY grievance are still on `v0.1`,
and three of the migration's open items exist only because of that. **This moves
both**, in two phases: PM-KISAN first, then PMFBY. They share one context
constant today, which is what makes the phases fictional until the first step
splits it -- see phase 1.

- Open items this closes: [../open-issues.md](../open-issues.md)
- The capability that proved v0.2: [../khc/plan.md](../khc/plan.md)

---

## How the chain actually runs

Written down first because two earlier drafts of this plan got it wrong. Each
row is what the code does, not what the shape of the spec suggests.

| Step | Carried in | Read by |
|---|---|---|
| Publish | `resourceAttributes`: `@context`, `@type`, `scheme`, `informationMode`, `grievanceOptions`, `actions[]` | the discovery service stores it |
| Discover | the jsonpath in `intent.filters`, and `context.networkId` matched against the publish's `visibleTo` | the discovery service |
| on_discover | `message.catalogs[].resources[]` | the tool |
| What the tool takes from it | `provider.id`, `resource.id`, `grievanceOptions.categories`, `challenge.methods`, and `actions[].action` as a **presence check only** (`agents/tools/v2/aif.py:375`) | the tool |
| The next call | `POST {OAN_ADAPTER_URL}/{action}`, body a commitment or support envelope carrying `@context` and `@type` | the adapter |
| Which provider endpoint it hits | the registry's `ProviderSchema.actions[].path`, written by `setup.py` | the adapter |
| What validates it | the `@context` **in the payload** is fetched, then `x-oan-schema-by-action[action]` | `schemav2validator` |
| What builds the provider request | the registry's `actions[].mappings` URL, jsonata `request`/`response` | `jsonmapper` |

Two consequences worth stating plainly, because the plan is sized by them.

**`operationRef` does not drive anything at runtime.** `grep -rn operationRef
--include="*.go"` across `beckn-onix` returns nothing; the adapter never fetches
an `openapi.yaml`. In the pack `operationRef` is `format: uri` plus a string
pattern, never dereferenced, so a URL that 404s still validates. The three-way
reconciliation of catalog <-> OpenAPI <-> mapping is
`network-specs/scripts/check-api-separation.py:88-95`, and it covers only the
`deployable: false` drafts under `network-specs`, not the mappings the adapter
loads from `quick-start`.

**The registry's pack version is not what validates.** `SchemaRegistry.schemaUrl`
is advertising metadata, published onward by `oanfed`'s `schema_packs()`. The
validator resolves the pack from the payload's own `@context`, rewritten where
`extendedSchema_contextRewrite` names a prefix. That is why KHC works today on a
deployment whose registry still says v0.1 -- see "The registry row" below.

## Why v0.1 is the cause, not a coincidence

`Grievance/v0.1` is a closed list of per-provider schemas, and the `Grievance`
entry names a schema per action (`v0.1/attributes.yaml:18-37`):

```yaml
x-beckn-container-by-action: {catalog/publish: resourceAttributes, init: commitmentAttributes,
                              status: commitmentAttributes, support: channels}
x-oan-schema-by-action:      {catalog/publish: GrievanceCatalog, init: GrievanceInit,
                              status: GrievanceStatus, support: GrievanceSupport}
```

**There is no `confirm` and no `select`, for any provider.** That is the root
cause, and it is bigger than the missing `PMFBYInit` an earlier draft of this
plan named: a two-call OTP exchange and a two-call status read have no actions
to ride on v0.1 at all. It is why KHC had to be built on v0.2.

On top of that, two closed shapes bite specific open items:

| v0.1 | Where | What it forces |
|---|---|---|
| `PMKISANOption.code` is a closed enum, `[G001…G010]` | `v0.1/attributes.yaml:841` | thirteen of PM-KISAN's real grievance types, `CORRUPTION` among them, cannot be published. The tool carries a refusal branch instead |
| `PMKISANStatus` requires `case`, and `mustSend` lists `case.filedOn` | `v0.1/attributes.yaml:645` | the farmer must produce a filing date they were shown once. A wrong date is indistinguishable from having no grievance |

`Grievance/v0.2` maps all five actions to one `GrievanceCall`
(`v0.2/attributes.yaml:19-24`), which requires `@context` and `@type` and
nothing else.

**Be honest about what that trade is.** v0.2 does not relocate per-operation
validation to the OpenAPI contract -- nothing reads that contract at runtime. It
*removes* per-operation validation. After the move, what stands between a
farmer's words and KRPH is the mapper's jsonata and the provider's own 400.
That is the same position KHC is already in, and the same position every v1 call
was in, but it should be chosen knowingly rather than discovered later. See
risk 1.

## The last v1 call, and why it is here

Filing a PMFBY grievance on v2 still reaches the old v1 BAP.
`agents/tools/v2/pmfby_grievance.py:40` imports `_verify_pmfby_otp_with_bap`
from the **v1** module and `:349` calls it, which posts to `BAP_ENDPOINT/status`
(`agents/tools/pmfby_grievance.py:140, 410`). With `BAP_ENDPOINT` unset it
raises. So PMFBY cannot be cut over to v2 on its own, and this is the only
capability where that is still true.

It is here rather than in PMFBY's own plan because the fix is the pack move:
v0.2 has an `init` and a `confirm`, so the OTP has somewhere to go.

## The registry row, which is the part nobody has looked at

`SchemaRegistry` makes `capabilityCode` a unique index, so a deployment holds
**one pack version per capability** (`bin/setup.py:592-612`). What the running
registry holds:

| capabilityCode | registered version | who publishes v0.2 catalogs against it |
|---|---|---|
| `openagrinet:Grievance` | **v0.1** | KHC, AIF |
| `openagrinet:SchemeApplication` | **v0.1** | KCC, AIF, PMFBY insurance |

`PROVIDERS` is iterated in insertion order and `pmkisan` is first, so v0.1 won
and stayed. `ensure_capability_schema` refuses to move a row already held: it
prints a warning and leaves it (`setup.py:604-612`).

This has no runtime effect, because validation resolves the pack from the
payload. But the deployment currently **advertises v0.1 for a capability half
its providers serve at v0.2**, and that is what federation peers read. So the
move does not only close open items -- it removes a live inconsistency.

It also means phase 1 needs a step that has nothing to do with code: **delete
the `openagrinet:Grievance` SchemaRegistry row**, or re-running
`register-providers` will leave v0.1 in place and print a warning into a log
nobody reads.

## What the catalog edit actually is

Smaller than two earlier drafts claimed, and load-bearing rather than optional.

Both catalogs were checked field by field against `GrievanceFields`
(`v0.2/attributes.yaml:382-390`). Neither carries a field
`unevaluatedProperties: false` would reject, and neither is missing a required
field -- **except `actions[].operationRef`**, which v0.2 requires on every
action. They carry `schema` and `attributesAt` today, and v0.2 allows both.

| | PM-KISAN | PMFBY | KHC (already v0.2) |
|---|---|---|---|
| `@context` | v0.1 | v0.1 | v0.2 |
| categories published | 10 | 1 (+1 sub-category) | 10 |
| action keys | `action, attributesAt, description, schema` | same | `action, description, operationRef` |
| `operationRef` | **missing** | **missing** | present |
| `offers` | none | `off:pmfby:grievance` | none |
| resource `descriptor` | **none** | **none** | `khc-grievance` |

`catalog/publish` is not named in v0.2's `x-oan-schema-by-action`, and an
unnamed action falls back to the whole type rather than being skipped
(`schemav2validator/extended_schema.go:759-775`). The whole type is
`GrievanceFields`. So **flipping `@context` without adding `operationRef` makes
the publish fail validation**, and discover with it, since on_discover carries
the same attributes. The two edits are one step, not two.

The shape to write is already in the pack. `v0.2/attributes.yaml` defines
`PMFBYCatalog` (`:59`) and `PMKISANCatalog` (`:207`) -- per-provider catalog
schemas with the operationRefs pinned by `enum`, for exactly the two providers
this plan moves. **Nothing `$ref`s either of them**; they are unreferenced in the
pack. Treat them as the author's intended catalog and lift the refs from there
rather than composing new ones. Both target contracts already exist
(`api/external/pm-kisan/grievance/v0.2/openapi.yaml`,
`api/external/pmfby/grievance/v0.2/openapi.yaml`) and already declare
`schema: .../Grievance/v0.2/attributes.yaml`. The escaped refs were checked
against the pack's pattern, including
`~1krphapi~1FGMS~1AddKRPHNCIPGrievenceSupportTicket/post`.

**The mappings need no `@context` edit.** Every grievance mapping echoes the
context the request carried -- `$ca.@context` in PM-KISAN's init and status,
`$ch.@context` in its support, `$attributes.@context` and `$channel.@context`
in PMFBY's two -- so none of them names a pack version
and none has to change. That is why KHC's mappings and PM-KISAN's are the same
shape while sitting on different packs. An earlier draft of this plan had a
step for it; there is nothing to edit.

While there: add `descriptor.code` to both resources. The contracts declare
`descriptorCode` (`pm-kisan-grievances`, `pmfby-grievances`) that no catalog
publishes, which is why these two discover by `@type` + `scheme.code` while KHC
and AIF discover by `descriptor.code`. One convention is better than two.

## PMFBY's categories: already published, already labelled

An earlier draft called this "the one thing that is real work" and "the one
dependency not ours to close". It was wrong, and the correction is worth keeping
rather than deleting, because the mistake is easy to repeat.

`grievance.support.yaml:9-11` hardcodes `$DEFAULT_CATEGORY_CODE := "3"`,
`$DEFAULT_SUB_CATEGORY_CODE := "10"`, and `pmfby_submit_grievance` takes no
category argument on either version, so every PMFBY grievance is filed under
category 3 / sub-category 10. That part is true. But the catalog already says
so, in the open:

```json
"categories": [{ "code": "3", "name": "Enrollment / Portal Issues",
  "help": "The only category this desk sends. PMFBY publishes no category list,
           so every grievance is filed under this one." }]
```

`grievanceOptions.categories` has `minItems: 1`, satisfied today. **The move is
not blocked on KRPH.** Asking for the real list is still worth doing, and it is
still the thing that would make PMFBY grievance route to the right desk, but it
is an improvement and not a prerequisite.

## Plan

Phases are ordered so each one is verifiable before the next starts.

### Phase 0 — ask, in parallel

1. Ask KRPH for PMFBY's real `ticketCategoryID` / `ticketSubCategoryID` list,
   and what `134306` as a `receiptSourceID` means.
2. Ask PM-KISAN how long its grievance OTP is valid (open item 4, which this
   does **not** close).

An earlier draft also asked PM-KISAN whether its status read can answer without
a filing date. **It already does.** The request half of `grievance.status.yaml`
sends `Type: "Reg_No_Status"` and `IdentityNo` and no date at all (`:4-7`); the
provider returns every record held against the registration, and the date is
applied locally in the response half. The contract says the same in its summary
-- "Read PM-KISAN grievances for a registration number", plural. Nobody needs to
be asked; see phase 1 step 7.

**Neither blocks anything.** Both phases below ship without an answer.

### Phase 1 — split the shared constant first, then PM-KISAN

**Yes, this moves both capabilities. But not at the same moment, and step 1 is
what makes that true.** `GRIEVANCE_CONTEXT` is defined once in
`v2/pmkisan_scheme_status.py:53` and imported by both grievance tools, so
flipping it in place would cut PM-KISAN and PMFBY over together, before PMFBY's
catalog or registry actions exist. Four references in total, so splitting it is
cheaper than sequencing around it.

1. Give PMFBY its own `GRIEVANCE_CONTEXT`: define it in
   `v2/pmfby_grievance.py` instead of importing it at
   `v2/pmfby_grievance.py:45`, still pointing at v0.1. Two lines, no
   behaviour change, and from here the two capabilities move independently.
   Phase 2 step 1 is then the only thing that moves PMFBY.
2. `GRIEVANCE_CONTEXT` at `v2/pmkisan_scheme_status.py:53` to the v0.2 context
   URL. This is now PM-KISAN's alone -- it is also imported by
   `v2/pmkisan_grievance.py:46`, which is the same capability.
3. `setup.py`'s `pmkisan` Grievance capability from `v0.1` to `v0.2`.
4. **Delete the `openagrinet:Grievance` SchemaRegistry row** before re-running
   `register-providers`, or the row stays at v0.1. Note what the row becomes:
   `pmkisan` is first in `PROVIDERS`, so it re-registers at v0.2 and `pmfby`
   then prints the warning instead. Right for PM-KISAN, KHC and AIF, wrong for
   PMFBY -- three of four rather than two. Phase 2 makes it four. That is a
   reason to keep the two phases close together, not a reason to merge them.
5. Edit the catalog in the api collection — **Grievance / PM-KISAN /
   Publish**, `catalogId` `catalog:grievance:pmkisan` — in one step: v0.2
   `@context`, an `operationRef` on each of `init`/`status`/`support` lifted
   from `PMKISANCatalog`, and a `descriptor.code` of `pm-kisan-grievances`.
   Flipping the context without the refs fails validation. The collection is
   this catalog's only publisher: nothing under
   `capability-examples/Grievance/publish/` covers PM-KISAN, and no make target
   publishes automatically.
6. Delete `_CATEGORY_CODES` and its refusal branch in `v2/pmkisan_grievance.py`,
   and publish the full category list — the ten plus the thirteen, collapsed as
   `../pmkisan/plan.md` argues (four or five headings, not thirteen). Separable
   from step 5 if it needs its own review.
7. Make `filed_on` optional. **This is three edits, not one, and the pack is
   the smallest of them.** An earlier draft said the mapping "returns the list
   unfiltered when no `case` is supplied". It does not:
   `grievance.status.yaml:53` filters on `$iso($r.GrievanceDate) =
   $ca.case.filedOn`, and with `filedOn` absent that predicate matches no
   record, so `$record` is undefined and `:61-64` answers the hardcoded 400
   *"No grievance matches this registration number and filing date."* The date
   is enforced in three places and all three have to move:
   - the tool returns `_NO_FILING_DATE_MSG` before it calls anything
     (`v2/pmkisan_grievance.py:490-492`, message at `:151`);
   - the mapping filters on it (`grievance.status.yaml:53`);
   - v0.1's `PMKISANStatus` lists `case.filedOn` in `mustSend` -- the only one
     this pack move removes by itself.

   What is left is a decision, not a blocker: the provider already returns every
   record, so with no date the mapping can answer with the newest one or with
   all of them. `commitmentAttributes.case` is a single object today and v0.2
   constrains nothing, so returning more than one needs a shape and a line in
   the tool's formatter (`_format_case`, `v2/pmkisan_grievance.py:198`). v1
   returned all of them. Separable from the rest of this phase.

**Verify:** api collection PM-KISAN folder green; then **re-run the tool**,
because a green collection does not prove the agent still reads the same
catalog row ([../khc/plan.md](../khc/plan.md), defect 4). Use a farmer sentence
that names no date, and one that names a category outside G001-G010. Check
PMFBY grievance too: step 1 is meant to leave it untouched, and that is the
claim being tested.

### Phase 2 — PMFBY to v0.2

1. PMFBY's own `GRIEVANCE_CONTEXT`, the one phase 1 step 1 created, to the v0.2
   context URL. This is the line that moves PMFBY, and after phase 1 it moves
   nothing else.
2. `setup.py`'s `pmfby` Grievance capability from `v0.1` to `v0.2`, plus two
   actions: `init` -> `/api/v1/services/nic/getOtp` and `confirm` ->
   `/api/v1/services/nic/verifyMobile`, both never retried — a retry sends or
   spends a second OTP.
3. `grievance.init.yaml` and `grievance.confirm.yaml`, copied from
   **`quick-start/config/mappings/pmfby/insurance.init.yaml` and
   `insurance.confirm.yaml`** — the deployed copies, 96 and 100-odd lines with
   `required:` guards. Not the files of the same name under
   `network-specs/api/external/pmfby/insurance/v0.2/mappings/`: those are
   13-line `deployable: false` sketches in a different format (`request.in`,
   `request.jsonata`), not an earlier version of the same file. The deployed
   pair already maps the exact endpoints PMFBY grievance needs, on the same host
   (`PMFBY_BASE_URL`), under `SchemeApplication/v0.2` — so this is a copy of two
   proven mappings, not new work against an unknown API.
4. The same single-step catalog edit to **Grievance / PMFBY / Publish**,
   `catalogId` `cat-pmfby-grievance`: v0.2 `@context`, `operationRef` on every
   action, `descriptor.code` of `pmfby-grievances`. `support` and `status` come
   from `PMFBYCatalog`; `init` and `confirm` point into the **insurance**
   contract, which is where `getOtp` and `verifyMobile` are declared. One host
   and one verification, so cross-referencing is honest and avoids two copies
   drifting. `grievanceOptions` needs no change.
5. Rewrite `initiate_pmfby_grievance_otp` and `check_pmfby_grievance_otp` as v2
   tools in `agents/tools/v2/pmfby_grievance.py`, keeping v1's names,
   signatures and docstrings.
6. Move their imports in `agents/tools/__init__.py:74` inside the
   `if _beckn_v2("PMFBY")` branch, and drop the `_verify_pmfby_otp_with_bap`
   import at `v2/pmfby_grievance.py:40`.
7. Pass the action gate that KHC and AIF already use. `v2/pmfby_grievance.py:137`
   and `v2/pmkisan_grievance.py:101` call `_first_resource(response.json(),
   who=...)` with **no `usable=` callback**, so they send an action whether or
   not the catalog advertises it. Add
   `usable=lambda c: _unusable(c, action)` to both — `_unusable` is already in
   `v2/aif.py:298` and needs importing — so the catalog and the
   registry cannot drift apart silently once `init` and `confirm` exist.

**Verify:** `BAP_ENDPOINT` unset, then file a grievance end to end against
`mock-pmfby`. That is the test — if it passes with no BAP configured, the last
v1 dependency is gone. Then the api collection, then the agent.

### Phase 3 — record

Close PM-KISAN items 2, 3 and 5 and PMFBY item 5 in
[../open-issues.md](../open-issues.md); correct PMFBY's stale `2 of 6` count and
Open TODO 6 (`SchemeApplication/v0.2` exists, those two tools are live) and
Open TODO 4 (phase 2 step 5 is that file); and record that `PMFBYCatalog` and
`PMKISANCatalog` are now the catalogs in use rather than unreferenced schemas.

Also split the "mapping drift" note, which has been one sentence covering two
different situations:

- **PMFBY insurance:** `network-specs/api/external/pmfby/insurance/v0.2/
  mappings/` holds four `deployable: false` sketches, 13 lines each, against
  96-152 line deployed files. Not drift — a sketch and an implementation that
  were never the same document. Say so, or delete the sketches.
- **PM-KISAN grievance:** the drafts are marked `status: "existing"` and are
  byte-identical to the deployed files for `init` and `status`. For `support`
  they are not, and the deployed copy is the poorer one — see open-issues.
  Two near-identical copies of a real mapping with no test that compares them
  is the condition that produced that bug.

## What this does not close

Said plainly, so the move is not oversold:

| Still open | Why this does not touch it |
|---|---|
| PM-KISAN's invented 10-minute OTP window (`grievance.init.yaml:28`) | a fact nobody has. Phase 0 asks |
| PMFBY grievances all file under category 3 / sub-category 10 | needs KRPH's list and a category argument on the tool. Phase 0 asks; the catalog already admits it |
| PMFBY and PM-KISAN never reached their real providers | no credentials. Not ours |
| The four prompt-fabrication bugs (items 13, 15, 16, 21) | identical on v1, parked by decision |
| Pages builds from `main`, so every canonical `v0.2` URL 404s | a merge, not code — and the bigger deployment risk than this move. It does not break the move, because `extendedSchema_contextRewrite` already redirects both v0.2 prefixes to `raw.githubusercontent` on `bv-migration`, and `operationRef` is never fetched |
| Re-enabling PM-KISAN's grievance lodge path | **unblocked** by phase 1, but still someone's decision. It writes to a government system |
| `openagrinet:SchemeApplication` is registered at v0.1 while KCC, AIF and PMFBY insurance all serve v0.2 | the same registry-row problem as Grievance, one capability over. Deliberately out of scope here, but it should be the next thing after this |

## Risks

1. **v0.2 validates almost nothing per operation.** `GrievanceCall` checks
   `@context` and `@type`. Nothing reads the OpenAPI contract at runtime, so the
   contract is a document, not a guard. The mitigation is not inside this move:
   extend `check-api-separation.py` to cover the deployed `quick-start`
   mappings, so catalog, contract and mapper are reconciled somewhere. Until
   that exists, the move trades a real check for a documented one.
2. **The two capabilities are only independent after phase 1 step 1.**
   `GRIEVANCE_CONTEXT` is defined once in `v2/pmkisan_scheme_status.py:53` and
   imported by both grievance tools, so until the split lands, any edit to it
   moves PMFBY as well -- before PMFBY has a v0.2 catalog or the registry
   actions to serve one. Do step 1 on its own and confirm PMFBY grievance still
   works before touching anything else. Skipping it does not make phase 1
   smaller; it makes phase 1 silently become phase 2.
3. **The `@context` flip and the `operationRef` edit must land together.**
   Separately, each one fails validation. Phase 1 step 5 and phase 2 step 4 are
   each a single commit.
4. **Changing `visibleTo` at the same time would be a second bug wearing the
   first one's clothes.** Both grievance catalogs publish `visibleTo:
   ["{{networkId}}", "{{otherNetworkId}}"]` and both tools discover with
   `OAN_NETWORK_ID`, default `oan.dev`
   (`v2/pmkisan_scheme_status.py:94`), so the pair agrees. KHC and AIF use the
   other self-consistent pair, `["*"]` against `OAN_PUBLIC_NETWORK_ID` default
   `"*"` (`v2/aif.py:81`), and **`*` is matched literally, not as a wildcard** --
   mixing them is what broke `check_khc_grievance_status` on 2026-11-24
   ([../khc/plan.md](../khc/plan.md), defect 4). Pack version and visibility are
   independent. Move one per change, or a failure cannot be attributed.
5. **PMFBY's grievance contract was never checked against the real KRPH API.**
   Writing its `operationRef`s into the catalog makes it look authoritative.
   It is not — see risk 2 in `../pmfby/plan.md`.
