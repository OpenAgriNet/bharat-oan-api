# AIF on v2 — try it yourself

Twenty minutes. Everything below ran green on 2026-11-23 against `mock-aif`.
**Nothing here has been run against the real AIF service**, and it cannot be
until someone supplies `AIF_BASE_URL` and `AIF_API_KEY` — AIF verifies a
farmer with an OTP to their registered mobile, which is why a mock was
authorised for this capability alone.

## Before you start

    cd helmcharts/quick-start
    make up
    make check-mappings          # the three AIF mappings must resolve

Two things AIF needs that no earlier capability did.

**Redis.** The session is not optional and there is no in-memory fallback by
design — a fallback would work on one replica and silently lose sessions on
two. `make up` starts it; the adapter will not serve AIF without it.

**A host for the v0.2 packs.** `SchemeApplication/v0.2` and `Grievance/v0.2`
are not published — `openagrinet.github.io` serves v0.1 only. Serve them from
the compose network and name the host:

    docker run -d --name spec-files --network oan-internal \
      -v /absolute/path/to/network-specs:/usr/share/nginx/html:ro nginx:alpine

    # quick-start/.env
    EXTRA_SCHEMA_DOMAINS=spec-files

    make configure-adapter

`EXTRA_SCHEMA_DOMAINS` is appended to the extended-schema allowlist. Keep it
empty everywhere else: a payload chooses its own `@context` URL, so every host
listed is one a caller can send the adapter to. Delete the line and the
container the day the packs are published.

The adapter is at `localhost:9201` from the host. v2 posts to the **consumer
adapter** at `/consumer/init` and `/consumer/status`.

The mock's OTP is `123456` (`MOCKAIF_OTP`). AIF's OTP is six digits — the v1
tool refuses anything else before it even makes the call. Beneficiary `999999`
is the one the mock has never heard of; an even `loanApplicationNumber` is not
found, and an even `beneficiaryId` has no support tickets.

## The journey, by hand

Four bodies ship with the stack:

    capability-examples/SchemeApplication/init/init-aif.json           send the OTP
    capability-examples/SchemeApplication/init/init-aif-verify.json    answer it
    capability-examples/SchemeApplication/status/status-aif.json       loan status
    capability-examples/Grievance/status/status-aif.json               support tickets

They carry the published `@context` URL, which does not exist yet. While that
is true, rewrite it on the way out:

    sed 's#https://openagrinet.github.io/network-specs/schema#http://spec-files/schema#' \
      capability-examples/SchemeApplication/init/init-aif.json \
      | curl -s -X POST http://127.0.0.1:9201/consumer/init \
          -H 'Content-Type: application/json' -d @-

**Send all four with one `transactionId`, in order.** The session is keyed on
it. Out of order, or with a fresh id, `status` is refused with "this request
needs a verified session and there is none" — which is the system working.

1. `init` with no `challenge` → AIF sends an OTP. You get back
   `challenge.issued` and the commitment stays `DRAFT`.
2. `init` with `challenge.response.value: "123456"` → the commitment goes
   `ACTIVE` and carries `applicant.name`. **The token is not in the answer**
   and never will be: it lives in Redis against the transaction, and putting
   it in a signed callback would hand a farmer's session to whoever reads the
   log.
3. `status` with a `loanApplicationNumber` → `application.status`.
4. `status` on the Grievance `@type` → `cases`, always an array. Empty means
   AIF checked and found nothing; that is an answer, not a failure.

Steps 3 and 4 spend the token step 2 bought. The Grievance binding has no OTP
of its own — `sessionBindingKey` points it at the SchemeApplication binding,
because AIF has one verification and one token.

### Refusals worth running

| what you send | what you should get |
|---|---|
| `beneficiaryId: "999999"` on `init` | `BIZ_GENERIC_ERROR` "AIF holds no beneficiary with that id." |
| a wrong OTP | `BIZ_GENERIC_ERROR` "AIF did not accept that OTP." |
| the same OTP twice | the same — an OTP is spent on first use |
| an even `loanApplicationNumber` | `BIZ_GENERIC_ERROR` "no loan application with that number" |
| `status` with a fresh `transactionId` | `SCH_INVALID_FORMAT` "needs a verified session" |
| `status` naming a different `beneficiaryId` than the one verified | `SCH_INVALID_FORMAT` "for a different beneficiary" |

The last two are the point of the whole design. A transaction id alone must
never be enough to ask about someone else's loan.

### Reading the errors

| prefix | whose fault | what to do |
|---|---|---|
| `BIZ_*` | AIF's own answer | pass it to the farmer |
| `SCH_*` | ours — the request was built wrong | fix the request |
| `NET_DOWNSTREAM_UNAVAILABLE` | upstream is down | retry later |

A refusal only counts as a real refusal if it is `BIZ_*`.

## Through the agent tool

The four tools in `agents/tools/v2/aif.py` make the same four calls. They are
off by default:

    # bharat-oan-api/.env
    AIF_BECKN_VERSION=v2
    OAN_ADAPTER_URL=http://network-adapter:9201/consumer
    OAN_SCHEMA_BASE=http://spec-files/schema     # only until v0.2 is published

`agents/tools/__init__.py` reads the flag at import time and binds all four
names to the v2 module; unset, they bind to `agents/tools/aif.py` and nothing
changes. All four move together because they share one OTP and one session —
a farmer who verified on v1 cannot spend that session on v2.

`OAN_SCHEMA_BASE` exists for the same reason as `EXTRA_SCHEMA_DOMAINS`: the
v0.2 packs are not published, so the `@context` the tool sends has to name the
host actually serving them. Both go away together.

The app container must be able to resolve `network-adapter` and `spec-files`,
which means it is on `oan-internal`.

The conversation is the session. `initiate_aif_otp`, `verify_aif_otp`, then
either status tool — all four derive one transaction id from the chat session
and the beneficiary ID, which is what the adapter keyed the OTP on. A farmer
verifies once and both status tools work for the rest of the conversation.
Asking about a *different* beneficiary gets a different transaction id, so it
is refused and the farmer is told to verify again. That is deliberate: a chat
session must not be a key to someone else's loan.

Two refusals never reach AIF — the tool checks them first, because AIF rejects
both outright: a beneficiary ID or loan number that is not plain digits, and
an OTP that is not exactly six digits. Regional-script digits are folded to
ASCII before that check, so a farmer typing in Devanagari is not refused.

## Swapping the mock for the real AIF

A `.env` edit and one command. No code, no template, no rebuild.

    AIF_BASE_URL=https://<the real host>/api
    AIF_API_KEY=<the real key>

    make register-provider PROVIDER=aif UPDATE=1

It prints the change it made:

    aif: updated
      baseUrl: http://mock-aif:9103 -> https://<the real host>/api

The base URL is registry data, not adapter config, so the adapter picks it up
on the next request. `AIF_API_KEY` reaches the adapter through its
environment — the config names the variable, never the value.

Expect one warning on every AIF registration:

    openagrinet:SchemeApplication: registered at v0.1, and this provider
    speaks v0.2. The registry holds one pack version per capability...

`SchemaRegistry` indexes on `capabilityCode` alone, so a deployment can
advertise exactly one pack version per capability. AIF is left out of that row
rather than overwriting PM-KISAN's. Nothing in the adapter reads it — each
payload names its own pack — but `oanfed.py:schema_packs()` publishes it, so
the network sees v0.1 until every provider of the code moves together.

## What you will not be able to try

- **Anything against the real AIF.** The response shapes the mappings read —
  the bare JSON string on `/Status`, the `Success`/`success` spelling split,
  `ExpiresIn` — come from the provider document, not from an observed
  response. A mock agrees with us by construction. This is the one thing it
  cannot catch.
- **Filing a grievance.** AIF publishes no filing call, so this capability
  answers `status` and nothing else. A `support` action would have no
  endpoint behind it.
- **The real token lifetime.** `sessionTtl: 1h` is the floor used when AIF
  states no `ExpiresIn`, and 60s is shaved off whatever lifetime is used so a
  token is not spent in the second it expires. Both numbers are ours.
