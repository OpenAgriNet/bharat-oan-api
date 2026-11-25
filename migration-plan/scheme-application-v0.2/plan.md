# PM-KISAN application status -> SchemeApplication/v0.2

Done 2026-11-25. PM-KISAN was the last `openagrinet:SchemeApplication`
provider on v0.1; AIF, KCC and PMFBY insurance were already on v0.2. The
capability now has one pack and every provider speaks it.

## Why it was small

Nothing had to be authored. `schema/SchemeApplication/v0.2/attributes.yaml`
and `api/external/pm-kisan/application/v0.2/openapi.yaml` were already in
`network-specs` on `bv-migration`, and that repo needed no commit at all.

v0.2 is generic in the same way `Grievance/v0.2` is: `x-oan-schema-by-action`
sends `init`, `confirm`, `select` and `status` all to `SchemeApplicationCall`,
which requires only `@context` and `@type` and takes `additionalProperties:
true`. v0.1 instead named `PMKISANApplicationInit` and
`PMKISANApplicationStatus` per action and validated the fields itself.

The mappings needed no change. `application-status.init.yaml` and
`.status.yaml` read `@context` off the request and echo it back
(`$ca.`@context``), so they are version-agnostic -- the live helmcharts copies
are byte-identical to the v0.2 copies in `network-specs`.

## What changed

| Where | Change |
|---|---|
| `agents/tools/v2/pmkisan_scheme_status.py:45,243` | dropped the hardcoded v0.1 constant, imported `scheme_application_context()` from `v2/aif.py` -- the function AIF, KCC and PMFBY already share |
| `agents/tools/v2/aif.py:51` | the comment said PM-KISAN and PMFBY stay on v0.1. Both have moved |
| `api-collection` -> `Application status/Publish` | catalog `@context` to v0.2, and both actions swapped `schema` for `operationRef` against the PM-KISAN application contract |
| `api-collection` -> `Application status/Status` (3 requests) | request `@context` to v0.2 |
| `quick-start/bin/setup.py:174` | the pmkisan SchemeApplication seed from `"v0.1"` to `"v0.2"` |
| registry `SchemaRegistry` row | `PUT` in place to v0.2 + the v0.2 `schemaUrl` |
| `capability-examples/SchemeApplication/{init,status}` | `@context` to v0.2 |
| `beckn-onix` local-validation compose, `appstatus_mappings_test.go` | v0.1 -> v0.2; the Go suite still passes |

`operationRef` is the only field v0.2 requires that v0.1 did not. `scheme`,
`challenge`, `informationMode` and `attributesAt` were already v0.2-shaped,
and PM-KISAN publishes no attribute `SchemeApplicationFields` does not name --
so `unevaluatedProperties: false` was not the trap it was for `grievanceOptions`.

The registry row was updated with a `PUT`, never a `DELETE`. The registry's
delete is soft: it keeps the row with `_status` false, the unique index still
holds the `capabilityCode`, and only direct SQL gets it back. See open-issues.

## Verification -- 2026-11-25

**Adapter.** Full newman suite: 99 requests, 327 assertions, 2 failures, both
SATHI and both pre-existing (no `SATHI_API_KEY`, open-issues item 3). The
`Application status` folder alone is 31 assertions, 0 failures.

The pack is genuinely enforced, not skipped. The adapter log for a status call
reads `Transformed .../SchemeApplication/v0.2/context.jsonld ->
.../v0.2/attributes.yaml` and then `action status on @type
openagrinet:SchemeApplication validates against SchemeApplicationCall`. v0.1
would have named `SchemeApplicationStatus` there.

**Tools.** `initiate_pm_kisan_status_check` then
`check_pm_kisan_status_with_otp`, by registration number and by mobile. Both
read back the instalment, the eKYC flag and the blockers. A wrong OTP returns
"the OTP is wrong or has expired"; no identifier returns the ask. The tool
reports `@context` as v0.2.

`GRIEVANCE_CONTEXT` is imported out of this module by `v2/pmkisan_grievance.py`,
so the grievance tools were re-run too: send-OTP and status both still answer.

**Agent.** Two conversations. "My PM Kisan registration number is UP77554433A,
can you check my status?" -> `initiate_pm_kisan_status_check`, then "The OTP is
1234" -> `check_pm_kisan_status_with_otp`, answered with the 9th instalment and
nothing blocking. A second run on the mobile path returned the two blockers
(Income Tax Payee, Land Seeding KYS) and explained them on a follow-up with no
further tool call.

Every answer came from `mock-pmkisan`. PM-KISAN's real endpoints have never
been reached from here.

## Left open

| Thing | Why |
|---|---|
| `PMKISANApplicationCatalog` at `SchemeApplication/v0.2/attributes.yaml:58` | 126 lines nothing `$ref`s, and it contradicts the catalog PM-KISAN actually publishes. Same dead-schema trap as the `PMFBYCatalog`/`PMKISANCatalog` pair in `Grievance/v0.2` -- open-issues item 26 |
| `beckn-onix/deploy/local-validation/docker-compose.yml` still mounts `Grievance/v0.1` | that harness is a developer's own, with absolute `/Users/...` paths, and is stale across most capabilities. Not on the runtime path |
| Nothing fetches `operationRef` at runtime | true of every v0.2 capability. The contract is advertised, not enforced |
