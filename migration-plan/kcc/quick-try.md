# KCC — try it by hand

> **This has been run end to end, 2026-11-24, against `mockkcc`.** The same
> journey is in the API collection as the `KCC - loan application status`
> folder — 12 requests, 45 assertions, all passing — and all three agent tools
> were run against the same stack.
>
> **We have no KCC contact**, so everything here runs against `mockkcc`. One
> thing the mock cannot prove is the AES key and its IV rule — see the last
> section.

Two Beckn calls, one transaction id, one OTP. That is the whole journey.

The fastest way to run all of it — registry, publish, discover, the journey and
every refusal — is the collection folder:

```bash
cd helmcharts/api-collection
npx --yes newman run OpenAgriNet.api-collection.json \
  -e environments/OpenAgriNet.api.local-environment.json \
  --folder "KCC - loan application status"
```

Expect 12 requests, 45 assertions, 0 failures. The rest of this page is the
same journey by hand, for when one of those assertions goes red.

## Before you start

| Need | How to get it |
|---|---|
| The stack up | `cd helmcharts/quick-start && make up` |
| KCC registered | `make register-provider PROVIDER=kcc` |
| The catalog published | `POST capability-examples/SchemeApplication/publish/ondemand-kcc.json` to the discovery service |
| A mobile number | **the mock's**, not a real one — see the warning below |

`mockkcc` keys its answer off the mobile's **last digit**, so every shape the
mapping has to survive is reachable without touching the mock:

| Last digit | What comes back |
|---|---|
| `0` | an empty list — answered, not refused |
| `1` | one SANCTIONED application |
| anything else | three, one of them REJECTED but filed under a `DRAFT` name |
| the whole number `9999999999` | `init` itself is refused: nothing filed for this mobile |

The OTP is always `123456`.

> **The real KCC host is production.** `https://fasalrin.gov.in/kccintegration`
> is not a UAT host. Every `init` against it sends a real SMS to whoever owns
> that number. Point `KCC_BASE_URL` at `mockkcc` unless the number is your own.

Check the registry has the two actions before anything else:

```bash
curl -s http://127.0.0.1:8081/api/v1/ProviderSchema/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"bindingKey":{"eq":"kcc|openagrinet:SchemeApplication"}}}' \
  | jq '.data[0].actions[] | {action, method, path, mappings}'
```

Expect `init` and `status`. **No `select`** — that one never leaves the tool.
The binding record carries no version; see the last row of the error table.

## The journey, by hand

Pick one transaction id and keep it for both calls. The adapter keys the
session on it; two different ids means the second call finds no `requestID`.

```bash
ADAPTER=http://127.0.0.1:9201/consumer
TXN=$(uuidgen)
MOBILE=9876543212
NET=oan.dev
SCHEMA=https://openagrinet.github.io/network-specs/schema
```

The Beckn envelope is `domain: agriculture`, `version: 2.0.0` — that is the
*protocol* version and it is not negotiable; the adapter answers
`SCH_INVALID_ENUM … value must be "2.0.0"` for anything else. **v0.2 is the
schema pack version**, and it appears only inside `@context`.

`networkId` has to be the network KCC published to, not `*`.

### 1. `init` — ask for the OTP

```bash
curl -s "$ADAPTER/init" -H 'Content-Type: application/json' -d "{
  \"context\": {
    \"domain\": \"agriculture\", \"action\": \"init\", \"version\": \"2.0.0\",
    \"networkId\": \"$NET\", \"senderId\": \"$NET\", \"receiverId\": \"$NET\",
    \"transactionId\": \"$TXN\", \"messageId\": \"$(uuidgen)\",
    \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
  },
  \"message\": { \"contract\": { \"id\": \"$(uuidgen)\", \"commitments\": [{
    \"status\": { \"descriptor\": { \"code\": \"DRAFT\" } },
    \"offer\": { \"id\": \"off:kcc:loan\",
      \"provider\": { \"id\": \"kcc\", \"descriptor\": { \"name\": \"Kisan Credit Card\" } },
      \"resourceIds\": [\"res:kcc:loan\"] },
    \"resources\": [{ \"id\": \"res:kcc:loan\", \"quantity\": { \"count\": 1 } }],
    \"commitmentAttributes\": {
      \"@context\": \"$SCHEMA/SchemeApplication/v0.2/context.jsonld\",
      \"@type\": \"openagrinet:SchemeApplication\",
      \"informationMode\": \"OnDemand\",
      \"scheme\": { \"code\": \"KCC\", \"name\": \"Kisan Credit Card\" },
      \"mobileNumber\": \"$MOBILE\",
      \"otpFor\": \"krishika\"
    }
  }] } }
}" | jq '.message.contract.commitments[0].commitmentAttributes'
```

What comes back, verbatim from the run above:

```json
{
  "@context": "…/SchemeApplication/v0.2/context.jsonld",
  "@type": "openagrinet:SchemeApplication",
  "informationMode": "OnDemand",
  "scheme": { "code": "KCC", "name": "Kisan Credit Card" },
  "mobileNumber": "9876543212",
  "requestID": "RQ-9014",
  "challenge": {
    "issued": {
      "method": "OTP", "channel": "SMS", "length": 6, "valueType": "NUMERIC",
      "sentTo": "XXXXXX3212",
      "expiresAt": "2026-11-24T07:45:07.167Z"
    }
  }
}
```

Three things to notice. `mobileNumber` and `otpFor` are **KCC's own field
names**, not Beckn ones — the catalog's init `operationRef` points at the
operation that declares them, and the mapping passes them straight through. The
masked number is at `challenge.issued.sentTo`; `mobileNumber` is the one you
sent, echoed back unmasked, because it is your own input. And the status is
still `DRAFT`: nothing has been verified yet.

Under this, three things happened that the response does not show: the adapter
logged in to `/auth/login` (form-encoded, token cached an hour), it sealed
`{mobileNumber, otpFor: "krishika"}` into an AES-256-CBC hex blob, and it held
`requestID` against `$TXN` bound to `$MOBILE` for five minutes.

### 2. `status` — answer the OTP, get the applications

Same `$TXN`. Send `requestID` back, and the six digits from the SMS — `123456`
on the mock.

```bash
REQ=RQ-9014   # whatever init returned
```

The body is the `init` body with `action` set to `status` and two fields added
to `commitmentAttributes`:

```json
"requestID": "$REQ",
"otp": 123456
```

`otp` is a **number**. KCC's contract declares it an integer and refuses the
quoted form. The mapping casts a string through `$number()`, so `"123456"`
works too — but the field name and the type both come from the contract, not
from anything written in the tool or this file.

```bash
… | jq '.message.contract.commitments[0] |
        {status: .status.descriptor.code,
         applications: .commitmentAttributes.applications}'
```

The commitment flips to `ACTIVE` — that is the OTP being accepted — and the
applications come back as a list, always, even when it is empty:

```json
{
  "applicationNo": "100245",
  "status": { "code": "SANCTIONED", "name": "SANCTIONED" },
  "requestedAmount": 150000,
  "sanctionedAmount": 140000,
  "bank": { "name": "State Bank of India", "branch": "Hubballi Main" },
  "updatedOn": "2026-10-14T10:05:00+05:30",
  "history": [
    { "status": "SUBMITTED",    "on": "2026-09-30T09:00:00+05:30" },
    { "status": "UNDER_REVIEW", "on": "2026-10-05T11:20:00+05:30" },
    { "status": "SANCTIONED",   "on": "2026-10-14T10:05:00+05:30" }
  ]
}
```

`applicationNo` is text even though KCC sends a number, and `requestedAmount` is
a number even though KCC sends a digit string — the mapping settles both, so
the tool never has to guess.

The rejected row is the one worth reading. KCC files a rejected application back
under a `DRAFT` name, so `status.name` says `DRAFT` while `status.code` says
`REJECTED`:

```json
{
  "applicationNo": "100247",
  "status": { "code": "REJECTED", "name": "DRAFT" },
  "rejectionReason": "Land records do not match the applicant name",
  "bank": { "name": "Karnataka Gramin Bank", "branch": "Dharwad" }
}
```

The tool reads `status.code`, which is why a farmer is told their application
was rejected and why, rather than that it is a draft.

That OTP is now spent. There is no third call.

## Refusals worth running

Each of these should be refused before KCC is touched. If one of them reaches
the provider, the binding is wrong.

| Try | Expect | Why |
|---|---|---|
| `status` with a fresh `transactionId` | refused, no session | the session is keyed on the transaction id |
| `status` with a different `mobile` than `init` used | refused | `sessionBindAt` — a transaction id must not be a key to another farmer's flow |
| `status` six minutes after `init` | refused, session expired | `sessionTtl: 5m`, matching the OTP's own 300s |
| `init` twice on one transaction id | second one re-sends the OTP and replaces the held `requestID` | the farmer did not get the SMS; this is the retry path, not an error |
| `status` twice with one OTP | **unknown — write down what happens** | this is open item 5. The plan assumes the second one fails |
| a five-digit OTP through the tool | refused by the tool, no call made | `len(otp) != 6` guard, kept from v1 |
| a **wrong** six-digit OTP | HTTP 400, `BIZ_GENERIC_ERROR`, KCC's own wording, `error.details.path` naming `commitmentAttributes.otp` | proves `envelopeResponseOptional` is on. If you get a 502 here, the flag is missing |
| a `requestID` KCC never issued | HTTP 400, `SCH_INVALID_FORMAT`, "needs a verified session" | the adapter's session gate refuses it before KCC is called, so a guessed `requestID` cannot be spent on another farmer's transaction |
| `init` without `otpFor` | HTTP 400, the mapping's `required` message naming `otpFor` | the contract fixes it at `const: krishika`, so the tool always fills it; a hand-written body can leave it out |
| `status` without `otp` | HTTP 400, the mapping's `required` message naming `otp` | the `required` check runs before the session gate, so the message says what is missing rather than what is unverified |
| `select` through the network | HTTP 400, `SCH_INVALID_FORMAT`, `kcc\|openagrinet:SchemeApplication does not serve action "select"; it serves init, status` | `select` is tool-side only and is deliberately not registered. The refusal names the two actions that do exist, so it is self-explaining |

## Reading the errors

| What you see | What it means | First thing to check |
|---|---|---|
| `carries no future data.sessionTTL` on startup | `tokenExpiryField` was configured instead of `tokenTtl` | the template — KCC's `sessionTTL` is relative, so it must be `tokenTtl: 1h` |
| `401` on every call | the login failed, so the `token` header is empty | `KCC_ENTITY_TYPE` / `KCC_USER_ID` / `KCC_PASSWORD`; and that `tokenEncoding: form` is set — KCC rejects a JSON login |
| `BIZ_GENERIC_ERROR` carrying KCC's own OTP wording | working as designed | this is what a wrong OTP looks like: HTTP 400, with `error.details.path` pointing at `commitmentAttributes.otp`. Retype the code |
| `NET_DOWNSTREAM_UNAVAILABLE` on a **wrong OTP** | `envelopeResponseOptional` is missing from the KCC binding | the codec found no `data` in KCC's error body and turned it into a 502 before the mapping ran. Add the flag; the farmer should be told to check the code, not that KCC is down |
| `answered without an envelope` | same cause, stated plainly | as above |
| garbled plaintext, or KCC rejecting a well-formed call | the IV rule is wrong | **open item 1, the one real blocker.** The mock cannot catch this — it shares the guess |
| `applications` empty when the farmer has one | the three-shape trap | the decrypted `data` can be one object, an array, or `{applications: […]}`. And `[response.data]` on an empty array yields `[[]]` — guard with `$exists(response.data[0])` |
| session refused on a call you think is right | the two calls used different transaction ids | `$TXN` must be identical; the tool derives it from `session_id` + mobile, so it is stable by construction |
| `the envelope for kcc\|openagrinet:SchemeApplication is not configured on this adapter` | the `KCC_*` variables never reached the adapter **container** | not the template — `quick-start/docker-compose.yml`. `KCC_USER_ID`, `KCC_PASSWORD`, `KCC_ENCRYPTION_KEY` and `KCC_ENCRYPTION_IV` must be listed under **both** `provider-adapter` and `network-adapter`, then recreate the container. The adapter logs the real reason (`KCC_ENCRYPTION_KEY is not set`) on a separate line from the 500 it returns |
| `SCH_INVALID_FORMAT` … `needs a verified session` on `status` | the adapter is holding no `requestID` for this transaction | either `init` was never run on this `$TXN`, or the `requestID` in the body is not the one `init` returned. This is the session gate doing its job, not a mapping fault |
| the registry's capability row still says `0.1` while KCC sends `0.2` | expected | the registry holds **one** pack version per capability and PM-KISAN pinned `SchemeApplication` at v0.1. KCC's v0.2 identity lives in the catalog `@context` and the mapping, and the `ProviderSchema` binding record has no version field at all |

## Through the agent tool

The tools run inside the `sva_app` container — it bind-mounts the repo at
`/app` and sits on `oan-internal` with the adapter, so there is nothing to
install. There is no virtualenv on the host; running them there fails on
`No module named 'httpx'`.

```bash
docker exec \
  -e OAN_ADAPTER_URL=http://network-adapter:9201/consumer \
  -e OAN_NETWORK_ID=oan.dev \
  -e KCC_BECKN_VERSION=v2 \
  -w /app sva_app python3 - <<'PY'
import asyncio
from agents.tools.v2.kcc import (
    initiate_kcc_otp, check_kcc_application_status, select_kcc_application)

class Ctx:  # the tools only ever read session_id off this
    context = type("C", (), {"session_id": "try-it-by-hand"})()

async def main():
    print(await initiate_kcc_otp(Ctx(), mobile_number="9876543212"))
    print(await check_kcc_application_status(
        Ctx(), mobile_number="9876543212", otp="123456"))
    print(await select_kcc_application(
        Ctx(), mobile_number="9876543212", application_no="100245"))

asyncio.run(main())
PY
```

`OAN_NETWORK_ID` matters: `visibleTo` is matched by array overlap, and the KCC
catalog is published to this network rather than to `*`, so a discover that
does not name the network finds nothing.

Set `KCC_BECKN_VERSION=v2` first. All three tools move together: they share one
OTP, so a farmer who started on v1 cannot finish on v2.

### Where the field names come from

Watch the log on the first call of each action:

```
KCC v2 init   request fields from …/kcc/loan/v0.2/openapi.yaml: {'mobileNumber': 'subject.mobile', 'otpFor': 'fixed'}
KCC v2 status request fields from …/kcc/loan/v0.2/openapi.yaml: {'requestID': 'session', 'otp': 'challenge.otp', 'mobileNumber': 'subject.mobile'}
```

That line is the design working. The tool fetched the file the catalog's
`operationRef` names and read the field names out of it; `mobileNumber`,
`otpFor`, `requestID` and `otp` appear nowhere in `kcc.py`. Add a field to the
contract with an `x-oan-from` and the tool fills it on the next call. Make one
required with no `x-oan-from` and the tool refuses, naming it, rather than
sending a body KCC will reject.

If the log shows the fields being read from `http://spec-files/…` instead of
the published address, that is `PROVIDER_OPENAPI_URL_REWRITE` doing its job —
see below.

### Trying a spec change before it is published

The catalog names the published spec, which a local stack may not be able to
reach, and which does not yet have your edit in it. Point the fetch somewhere
else without touching the catalog:

```bash
PROVIDER_OPENAPI_URL_REWRITE=https://openagrinet.github.io/network-specs/=http://spec-files/
```

The `spec-files` container serves the `network-specs` working tree, so the tool
reads your edit. The value is a comma-separated list of `from=to` prefix pairs.
Deployment wiring, like the provider base URL — nothing in the catalog, the
registry or the tool changes.

The tool caches each document for an hour, so restart `sva_app` after editing a
spec, or set `PROVIDER_OPENAPI_CACHE_TTL_SECONDS=0`.

The third call is the one worth watching, because it proves the design:

```python
await select_kcc_application(ctx, mobile_number="9876543210", application_no="KCC2024001")
```

Watch the adapter log. **Nothing should appear.** `select` reads the list the
`status` call already cached against the transaction id. If you see a call go
out, the cache is not being written.

## Swapping the mock for the real KCC

No code, no rebuild. Three things:

1. `.env` — point `KCC_BASE_URL` and `KCC_TOKEN_URL` at
   `https://fasalrin.gov.in/kccintegration`, and fill in `KCC_ENTITY_TYPE`,
   `KCC_USER_ID`, `KCC_PASSWORD`, `KCC_ENCRYPTION_KEY`.
2. `make register-provider PROVIDER=kcc UPDATE=1`.
3. Use **your own** registered mobile number. It is production; the SMS is real.

If step 3 fails with something the error table does not cover, it is almost
certainly open item 1 — the IV rule — because that is the one thing the mock
agreed with itself about.

## What you will not be able to try

- **The real cipher. This is the one that matters.** The mock and the codec are
  built from the same guess about the IV rule, so they will agree with each
  other whether that guess is right or wrong. Only the real host settles it,
  and we have no contact to ask. Everything else below is a footnote next to
  this.
- **KCC's actual error wording.** The mock's `Invalid OTP` is invented. The
  *shape* is handled either way by `envelopeResponseOptional`, so this does not
  block anything — but do not promise a farmer any exact phrase.
- **A second `status` on one OTP.** The mock does whatever we made it do, which
  tells you nothing about KCC.
- **Anything about KHC.** Different provider, different plan, seven tools.
