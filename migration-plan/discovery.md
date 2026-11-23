# Discover before you call

**Status:** done for PM-KISAN and AIF. AIF now publishes an
`AgricultureResource/v0.2` catalog and all four of its v2 tools run end to end.
PMFBY has no v2 tool to wire. Verified against the live stack on 2026-11-23.

## The problem

Every v2 scheme tool carried the provider id and the resource id as module
constants:

    PROVIDER = {"id": "pmkisan", "descriptor": {"name": "PM-KISAN"}}
    _APPLICATION_RESOURCE = "res:pmkisan:application-status"

That is the catalog, copied into the client. It is right until a provider
re-publishes under a new resource id or a second provider offers the same
capability — and then the only fix is a release of the app.

The network already answers this question. `discover` returns the catalog:
which provider serves a capability, and under which resource id.

## What changed

The scheme tools now do what `mandi.py`, `weather.py` and `sathi_seed.py`
already did. Each module owns its own discover function, built on the transport
it already had, and no shared module was added:

    found = await _discover_application()
    if not found:
        return _UNAVAILABLE_MSG
    body = commitment_envelope(..., found)

`found` is `{"resource": ..., "provider": ...}` read straight off
`message.catalogs[*]`. The envelope takes `found["provider"]` and
`found["resource"]["id"]`.

| file | its discover functions |
|---|---|
| `agents/tools/v2/pmkisan_scheme_status.py` | `_discover_application` |
| `agents/tools/v2/pmkisan_grievance.py` | `_discover_grievance`, used on init, status and support |
| `agents/tools/v2/aif.py` | `_discover_loan`, `_discover_tickets` |

No provider or resource constant is left in any of them.

## Three decisions

**The scheme code is half of the filter.** Matching on `@type` alone returns
every scheme publishing the capability. PM-KISAN and PMFBY both publish
`openagrinet:Grievance`, so a tool taking the first hit would reach the wrong
desk. The filter is:

    $.catalogs[*].resources[*] ? (
      @.resourceAttributes."@type" == "openagrinet:Grievance" &&
      @.resourceAttributes.scheme.code == "PM-KISAN")

The three Discover requests in the Postman collection send the same
expression, so the collection and the tool agree on what discovery means.

**Offer ids stay constants.** `discover` returns catalogs and resources and
never offers — there is nothing on the wire to read an offer id from. Weather
already handles it this way (`weather.py:_OFFER_ID`).

**No catalog means no answer.** When discover returns nothing the tool says the
capability is unavailable and logs a warning. There is no fallback to constants:
a fallback is the hardcoding this change removed, and it would hide a provider
that has silently dropped out of the catalog. Sathi has no catalog in this
stack, so its tools answer "not available" for that reason and no other. AIF v2
is behind `AIF_BECKN_VERSION`, default `v1`, so nothing a farmer uses changes.

## What is published today

    networkId  provider    catalog                                 resource
    oan.dev    pmkisan     catalog:scheme-application:pmkisan      res:pmkisan:application-status
    oan.dev    pmkisan     catalog:grievance:pmkisan               res:pmkisan:grievance
    oan.dev    pmfby       cat-pmfby-grievance                     res:pmfby:grievance
    *          aif         cat-aif                                 res-aif-loan-status, res-aif-tickets
    *          agmarknet   catalog:mandi-price:MH                  resource:mandi-price:market:…
    *          mausamgram  catalog:weather-observation:mausamgram  resource:weather-observation:…

Scheme catalogs are published to `oan.dev`; AIF, mandi and weather to `*`.
`visibleTo` is matched by array overlap, so the audience asked for has to be
the one the catalog was published against. The tools that discover a `*`
catalog therefore ask on `*`, through one shared constant:

```python
_DISCOVER_NETWORK_ID = os.getenv("OAN_PUBLIC_NETWORK_ID", "*")
...
"networkId": _DISCOVER_NETWORK_ID if action == "discover" else network_id,
```

`weather.py`, `mandi.py` and `aif.py` all spell it this way. `mandi.py` did not:
it sent `OAN_NETWORK_ID` on discover, matched nothing against an agmarknet
catalog published to `*`, and returned NO_DATA for every location without ever
calling agmarknet. Fixed on 2026-11-23; it was not caught earlier because the
tool reports an empty discover as "no prices", which is indistinguishable from a
mandi that genuinely had no arrivals.

## How AIF publishes

AIF publishes as `AgricultureResource/v0.2`, not as `SchemeApplication/v0.2`.
Both of its capabilities are one catalog with two resources, told apart by
`descriptor.code` rather than by `@type`:

| resource | descriptor.code | actions advertised |
|---|---|---|
| `res-aif-loan-status` | `aif-loan-status` | `init`, `status` |
| `res-aif-tickets` | `aif-support-tickets` | `status` |

The catalog is `capability-examples/AgricultureResource/publish/ondemand-aif.json`
in `helmcharts`, and the API collection publishes and discovers it under
**AIF - loan and grievance status**.

`aif.py` reads the actions off the discovered resource and refuses to send one
the catalog does not advertise, rather than discovering that upstream:

```
AIF v2 res-aif-tickets does not advertise init; catalog offers ['status']
```

**Why not `SchemeApplication/v0.2`.** That pack pins `@context` with `const:` to
`https://openagrinet.github.io/network-specs/schema/SchemeApplication/v0.2/context.jsonld`,
and github.io does not serve v0.2 — 404. Three ways were tried and all refused:

| `@context` sent | refusal |
|---|---|
| the github.io v0.2 URL | `SCH_SCHEMA_ADAPTATION_FAILED` — the adapter resolves the pack from this URL and github serves 404 |
| a local `http://spec-files/...` URL | `SCH_TYPE_NOT_SUPPORTED: value must be an array` — the string branch is `const` the github URL |
| both, as an array | `SCH_TYPE_NOT_SUPPORTED: value must be a string` — the base Beckn spec declares `@context` as `type: string` |

`AgricultureResource/v0.2` has no such `const`, so it publishes cleanly from the
raw `bv-migration` branch. v0.1 was not an option either: `SchemeApplication/v0.1`
pins `scheme.code` to `const: PM-KISAN`, and `Grievance/v0.1` is a closed
`oneOf` over PMFBY and PM-KISAN only.

The *action* payloads still carry `SchemeApplication/v0.2` and `Grievance/v0.2`,
because registry bindings are keyed `<provider>|<@type>`. Those are reachable
through a narrow rewrite on the adapter — see below.

### The contextRewrite, and when to delete it

`network-adapter.yaml.tmpl` sets `extendedSchema_contextRewrite` on the two
blocks where `extendedSchema_enabled: "true"`:

```
https://openagrinet.github.io/network-specs/schema/SchemeApplication/v0.2/|https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/schema/SchemeApplication/v0.2/,
https://openagrinet.github.io/network-specs/schema/Grievance/v0.2/|https://raw.githubusercontent.com/OpenAgriNet/network-specs/refs/heads/bv-migration/schema/Grievance/v0.2/
```

It is a pre-existing key of the `schemav2validator` plugin (ADR-0003), not a new
one. It changes **where a pack is fetched from, never the payload's `@context`**,
and the first matching prefix wins. It is deliberately narrow: only those two
v0.2 prefixes, so PM-KISAN and PMFBY on v0.1 keep reading from github.io exactly
as before.

**Delete it when** `https://openagrinet.github.io/network-specs/schema/SchemeApplication/v0.2/attributes.yaml`
returns 200. GitHub Pages publishes from `main` (`network-specs/.github/workflows/pages.yml`),
so that happens when `bv-migration` merges to `main`.

### Known gap: the operationRefs 404

The catalog's `actions[].operationRef` name `openapi.yaml`, but AIF only has
`openapi-draft.yaml` on disk, so those URLs 404 today. The v0.2 pattern
`^https?://[^#]+/openapi\.yaml#/paths/…$` forbids the `-draft` name, so the
catalog cannot name the file that exists. `aif.py` never fetches them, so the
journey works; promoting the drafts is a `network-specs` change gated by
`scripts/check-api-separation.py`, which also wants a full `catalog.json`, an
`api/providers/index.html` section and a github.io `@context`.

## PMFBY has no v2 tool

`agents/tools/v2/` has no `pmfby_*` module. PMFBY is still on v1
(`agents/tools/pmfby_scheme_status.py`, `agents/tools/pmfby_grievance.py`),
and v1 has no discover action at all — it posts `/search` at the BAP. Its
grievance catalog is published and discoverable, so the moment PMFBY gets a v2
module it needs the same `_discover_*` function the other two have.

## Verified

Against the live stack on 2026-11-23. Upstreams are real except AIF, which runs
against `mock-aif` (the real portal needs an OTP to a registered mobile), and
PM-KISAN, which runs against `mock-pmkisan` for the same reason.

| sweep | result |
|---|---|
| cross-provider discovery (source assertions + live discover) | **17 passed, 0 failed** |
| AIF journey and refusals, 16 scenarios | **16 passed, 0 failed** |
| Postman `AIF - loan and grievance status` (publish, discover, journey, refusals) | **41 assertions, 0 failed** |

- No `PROVIDER` constant, no `res:` literal and a `found["provider"]` read in
  all three modules — asserted against the source, not by eye.
- PM-KISAN SchemeApplication and Grievance come back with the provider id and
  resource id the catalog holds.
- AIF loan and tickets come back with `aif` / `res-aif-loan-status` and
  `res-aif-tickets`, and the tickets resource refuses an `init` it does not
  advertise.
- The AIF journey runs 1→4 on one session: OTP sent with a masked mobile, OTP
  verified, loan application 201 reported as Disbursed, 2 support tickets read.
  Six refusals behave: unknown beneficiary, wrong OTP, loan status with no
  session, tickets with no session, another beneficiary on my session, and a
  non-numeric beneficiary id.
- Both PM-KISAN tools run end to end through the adapter on the discovered
  provider: `initiate_pm_kisan_status_check` and `pmkisan_grievance_send_otp`
  both send an OTP.
- Mandi returns real agmarknet prices at Pune and Ahmednagar after the
  `networkId` fix, and NO_DATA at Nashik — correctly, because the example
  catalog publishes only those two markets and Nashik is outside the 50 km
  radius. Publishing the remaining states is a separate task.
- `agents/tools/__init__.py` still binds v1 by default and v2 under the flags.

**Weather is unavailable upstream, not broken here.** The adapter reaches IMD
and gets `200 OK, 670 bytes` carrying `fcstday1..5` as empty objects, which the
tool reports as a provider failure rather than as an absence of forecast. This
predates the v2 work — see the note in
`config/mappings/mausamgram/weather-observation.select.yaml` and
`migration-plan/weather/plan.md`. The schema validator fetches
`WeatherObservation/v0.1` from github.io unchanged, so the contextRewrite above
does not touch this path.

Postman: failure counts on the Grievance and Application status folders are
unchanged (16 and 11), all of them the pre-existing one-shot-OTP and
`net-a.oan.dev` keyset issues.
