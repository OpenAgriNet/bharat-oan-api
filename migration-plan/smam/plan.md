# SMAM application status: v1 -> v2

**Status: built and verified end to end on the local stack, 2026-11-25.** Behind
`SMAM_BECKN_VERSION`, default `v1`, so nothing here serves farmers on v2 yet.

A farmer gives an application number or a mobile number; the answer is each of their SMAM
(Sub-Mission on Agricultural Mechanization) applications, with every machine's current status
and status history. Provider is `smam`, capability `openagrinet:SchemeApplication`.

- Stack setup: [../stack-setup.md](../stack-setup.md)
- Try it: [quick-try.md](quick-try.md)

---

## What v1 does

One `/search` to provider-backend, which makes one POST to SMAM's
`GetApplicationStatusByAI` with `{ "SearchValue": <value> }` and a `Token` header. Only the
value is sent: SMAM works out whether it is a mobile or an application number.
provider-backend turns the answer into one provider per application and one item per machine.

## How v2 works

```
publish (once)  cat-smam: one SchemeApplication resource, smam-benefits, action status, and its offer
discover        descriptor.code "smam-benefits" ──► the resource, its provider and its offer
status          commitmentAttributes.searchValue ──► adapter (Token header) ──► SMAM
on_status       SMAM's own answer, beside the pack fields in commitmentAttributes
```

- **Same tool, same arguments.** `check_smam_scheme_status(search_type, search_value)` keeps
  v1's signature and number cleaning, so the prompts do not change.
- **Only the value goes on the wire.** `search_type` is used to clean the value (a mobile becomes
  10 digits) and is not sent, as in v1.
- **The answer is passed through.** The helmcharts mapping returns SMAM's answer unchanged, and
  the tool renders it with v1's own models: every application, machine and status step.
- **The shared SchemeApplication helpers** in `agents/tools/v2/aif.py` build the envelope and
  pick the resource and its published offer, as for AIF and KCC.

## Where v2 differs from v1

Nothing the farmer sees. The text the model reads is v1's, including "Status: Failed. No
application found" when SMAM has nothing.

## What is built

| Repo | Branch | What |
|---|---|---|
| bharat-oan-api | `feat/smam-v2` | `agents/tools/v2/smam_scheme_status.py`, swapped in by `SMAM_BECKN_VERSION=v2` |
| helmcharts | `feat/smam-provider` | the `smam` provider (`status`, `Token` header), its pass-through mapping |
| network-adapter | — | none |
| network-specs | — | none; the resource and its OpenAPI are already published |

## What was verified

On the local stack, 2026-11-25: publish, discover and status through the adapter, and the tool
itself, for a value with five applications (several status steps each, one with none) and for
a value with no application. A unit test checks that the v2 text equals v1's for the same
answer.

## Open TODOs

1. **The spec's draft mapping** (`api/external/smam/benefit-status/v0.2/mappings/status.yaml`)
   accepts exactly one status step per machine and keeps only one status per application; real
   answers have several steps. Not used here; worth raising on network-specs.
2. **Extended (L2) schema validation is off** for the status call, as for the other v2 flows.
3. **`SchemeApplication` is registered at v0.1** in the registry while SMAM, AIF and KCC speak
   v0.2; the registry holds one version per capability.
4. **Links point at branches**: the `operationRef` and the mapping URL.
