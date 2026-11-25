# KHC on v2 — try it yourself

Thirty minutes. Everything below ran green on 2026-11-24 against `mock-khc`.

**Read this first.** KHC's filing *writes a real grievance to a real help
desk*. Every call below goes to the mock, and that is deliberate: a live filing
puts a fabricated complaint in front of a real officer and there is no delete.
The one host we have is UAT (`KHC_BASE_URL` in `/Users/manju/Documents/khc.env`,
never committed anywhere), and even UAT should be filed against once, on
purpose, with the desk owner watching. The read-only reference lists were
probed live; `support` never was.

## What rides the network and what does not

KHC is thirteen calls. They split three ways, and the split is the whole design.

| | calls | how |
|---|---|---|
| On the network | send OTP, verify OTP, file grievance, send status OTP, read grievance | adapter `init`, `confirm`, `support`, `select`, `status` |
| Left out on purpose | the ungated grievance read | `GET /v1/state/grievances/status?grievanceNo=` reads a grievance on a number alone, free text included. The gated pair above does the same read behind an OTP, so nothing is lost — [open-issues item 20](../open-issues.md) |
| Straight to KHC | states, districts, sub-districts, villages, schemes, categories, sub-categories | plain HTTPS from the app, like mandi's and weather's master data |

The seven reference lists are not a shortcut. A list of district names is not a
transaction and has no counterparty: wrapping it in a signed Beckn exchange buys
nothing and costs a round trip per keystroke of a farmer's address.

## Before you start

    cd helmcharts/quick-start
    make up

KHC needs three things.

**Redis.** The farmer's token is bought on `confirm` and spent on `support`,
and it lives in Redis against the transaction id. There is no in-memory
fallback by design. `make up` starts it.

**A host for the v0.2 packs.** `Grievance/v0.2` is not on
`openagrinet.github.io`. Same stopgap AIF uses:

    docker run -d --name spec-files --network oan-internal \
      -v /absolute/path/to/network-specs:/usr/share/nginx/html:ro nginx:alpine

    # quick-start/.env
    EXTRA_SCHEMA_DOMAINS=spec-files

    make configure-adapter

**The service key.** One variable holds the *whole* `Authorization` value:

    # quick-start/.env
    KHC_AUTH_HEADER=ApiKey local-khc-key

The adapter's static header scheme sends its variable verbatim — it adds no
prefix — so the prefix belongs in the value. `mock-khc` reads the same variable,
which means a drift between the two sides cannot be mistaken for a mapping
fault. The *farmer's* token is not here; that one is bought at runtime.

## The mock's data

`mock-khc` is on `127.0.0.1:9107` from the host, `mock-khc:9106` inside the
network. Its reference routes are under `/v1/state/reference/`, not
`/v1/state/`:

    curl -s 'http://127.0.0.1:9107/v1/state/reference/states' \
      -H 'Authorization: ApiKey local-khc-key'

The chain the examples use, every link of it read from the mock:

| level | id | name |
|---|---|---|
| state | 5 | Bihar |
| district | 501 | Bhagalpur |
| sub-district | 50101 | Naubatpur |
| village | 5010101 | Madhopur |
| scheme | 38 | Agriculture Infrastructure Fund (AIF) |
| division | 31 | Others |
| category | 39 | Bank Loan Sanction Issues |
| sub-category | 1726 | Bank refused loan under AIF |

Scheme 38 with its ten categories (39–48) and the six sub-categories of 39 are
copied from KHC UAT. Everything else is deterministic filler.

Three query parameters are not optional and the route answers nothing without
them: `schemes?stateId=`, `categories?scheme=`, `sub-categories?problemCategory=`.
Note the casing — the *parameters* are camelCase while the *fields they return*
are lowercase and unseparated (`stateid`, `subdistrictname`, `problemsubcategory`).
That is KHC's own inconsistency, kept.

Registered mobile is `9000000001` and the OTP is `123456`. **Every other
10-digit mobile also gets an OTP** — KHC does not refuse an unknown number on
send. The discriminator arrives on verify, as `isRegistered`.

## Publish the catalog

    curl -s -X POST http://127.0.0.1:9201/publish \
      -H 'Content-Type: application/json' \
      -d @capability-examples/Grievance/publish/ondemand-khc.json

Expect `200`, `ACCEPTED`, `itemCount 1`. Use `/publish`, not
`/provider/catalog/publish` — the latter wants a signature and answers
`401 AUT_SIGNATURE_MISSING`.

The catalog's `@context` is the canonical `openagrinet.github.io` URL and must
stay that way, even while the pack is served locally. `GrievanceFields.@context`
is a `oneOf` of that exact **const** or an array; rewrite it to
`http://spec-files/schema` and it fails the const, falls through to the array
branch, and is refused with `SCH_TYPE_NOT_SUPPORTED: value must be an array`.
Only the commitment and channel side may name a local base.

Then check it is there:

    curl -s -X POST http://127.0.0.1:9201/consumer/discover \
      -H 'Content-Type: application/json' -d '{...}' | grep khc-grievance

## The filing journey, by curl

Three bodies ship with the stack:

    capability-examples/Grievance/init/init-khc.json         send the OTP
    capability-examples/Grievance/confirm/confirm-khc.json   answer it
    capability-examples/Grievance/support/support-khc.json   file the grievance

**Send all three with one `transactionId`, in order.** The examples already
share one. The session is keyed on it, so a fresh id at step 3 is refused.

    sed 's#https://openagrinet.github.io/network-specs/schema#http://spec-files/schema#' \
      capability-examples/Grievance/init/init-khc.json \
      | curl -s -X POST http://127.0.0.1:9201/consumer/init \
          -H 'Content-Type: application/json' -d @-

1. `init` → KHC sends the OTP. You get `challenge.issued` with
   `{OTP, SMS, length 6, NUMERIC, expiresAt now+5min}` and the commitment stays
   `DRAFT`.
2. `confirm` with the code → the commitment goes `ACTIVE` and carries
   `applicant.registered`. **The token is not in the answer** and never will
   be: it is in Redis against the transaction.
3. `support` → `grievanceNo`, e.g. `BR/100006/6`.

### `support` carries no contract

This is the one shape that differs from every other capability. `SupportAction`
is `additionalProperties: false` over a single `support` object
(`beckn.yaml:2682-2698`), so a contract on this action is refused by the
protocol schema before any mapping runs. Everything rides
`message.support.channels[0]`, with `provider` at the channel's top level —
which is where the step's `providerIdAt` looks for it on this action.

Ten identifiers go out on the filing and every one is required. A defaulted
identifier does not fail; it files the grievance at the wrong desk and still
answers 200.

### The new-farmer branch

`confirm` answering `applicant.registered: false` is not a failure. KHC has not
seen this mobile before and wants a name and a gender on the filing — send
`applicantName` and `applicantGender` on the channel. Without them KHC refuses
with *"farmerName is required for a farmer not yet registered"*. For a farmer it
already knows, sending them is accepted but is more of the farmer's data on the
wire than the call needs, so the mapping omits them.

### Refusals worth running

| what you send | what you should get |
|---|---|
| a wrong OTP on `confirm` | `BIZ_GENERIC_ERROR` "KHC did not accept that OTP." |
| `support` with a fresh `transactionId` | `SCH_INVALID_FORMAT` "needs a verified session and there is none" |
| `support` naming a different `farmerMobileNumber` than the one verified | `SCH_INVALID_FORMAT` "this request is for a different beneficiary than the verified session" |
| `support` with `location.villageId` removed | "this capability needs location.villageId" |
| a new farmer with no `applicantName` | `BIZ_GENERIC_ERROR`, KHC's own wording |

The third one is the point of the design, and it did not work until
`firstValueAt` learned to read a comma-separated `sessionBindAt`
([open-issues item 22](../open-issues.md)). The token was bought for one mobile
number; without the check, a transaction id alone was enough to file a
grievance in another farmer's name. **The adapter image must be rebuilt for
this — tag `0.2.0` does not contain the fix.**

## The status journey, by curl

Two more bodies, and a journey of its own:

    capability-examples/Grievance/select/select-khc.json   send the status OTP
    capability-examples/Grievance/status/status-khc.json   answer it and read

Put the grievance number the filing returned into both, under
`commitmentAttributes.case.ticketNo`. **This is KHC's own number** — not the
`externalReferenceNo` we sent — because it is the one the desk answers to.

The two share a `transactionId` of their own. The status OTP is a separate
challenge from the filing one, so it does not reuse the filing's id.

1. `select` → KHC sends an OTP to the mobile the grievance was filed against.
   The answer echoes `case.ticketNo` and carries `challenge.issued`; the
   commitment stays `DRAFT` and **no status comes back**, because nothing is
   read by this call. A number no desk holds answers 404, so a 200 here is KHC
   saying it knows this one.
2. `status` with the code → the commitment goes `ACTIVE` and carries
   `case.ticketNo` and `case.status.code`, a `CaseStatusCode`. KHC's own phrase
   ("Open") does not travel: the mapping looks it up and sends `Registered`.

### Why `select` posts a query string

`check-status` is a POST whose only input is a query parameter. `grievanceNo` in
the body answers `400 "grievanceNo is required"`; the same value in the query
string reaches the grievance. A registry `Path` cannot hold a `?`, so the
mapping writes the field under the reserved **`_query`** key and the adapter
lifts whatever is there onto the URL — on a POST exactly as on a GET
(`util/query.go`, `Split`). `verify-otp` takes both of its values in the body.
The inconsistency is the provider's; each mapping follows the call in front of
it. **The adapter image must be rebuilt for this — tag `0.2.0` does not contain
`Split`.**

### Status refusals worth running

| what you send | what you should get |
|---|---|
| `select` for a grievance no desk holds | `BIZ_GENERIC_ERROR` "KHC could not send an OTP for that grievance number." |
| `select` with no `case.ticketNo` | `SCH_INVALID_FORMAT` "needs case.ticketNo" |
| `status` with a wrong OTP | `BIZ_GENERIC_ERROR` "KHC did not accept that OTP." |
| `status` with the **right** OTP and no `select` before it | the same refusal — and this is the one to run |

The last row is the gate. A real grievance and the correct code, refused,
because no `check-status` has asked for a code against that grievance. Run it
*before* the `select`: the challenge KHC holds is per grievance number and a
verify does not clear it, so once `select` has run for a number a correct OTP
against it keeps answering.

### Reading the errors

| prefix | whose fault | what to do |
|---|---|---|
| `BIZ_*` | KHC's own answer | pass it to the farmer |
| `SCH_*` | ours — the request was built wrong | fix the request |
| `NET_DOWNSTREAM_UNAVAILABLE` | upstream is down, or a credential is missing | check `KHC_AUTH_HEADER` reached the adapter |

That last one is worth naming: with `KHC_AUTH_HEADER` absent from the adapter's
environment, `init` fails with *"the header credential for khc is not
configured"* — which reads like an outage and is a missing variable.

## Through the agent tool

Seven tools in `agents/tools/v2/khc.py`, off by default:

    # bharat-oan-api/.env
    KHC_BECKN_VERSION=v2
    KHC_BASE_URL=http://mock-khc:9106
    KHC_AUTH_HEADER=ApiKey local-khc-key
    OAN_ADAPTER_URL=http://network-adapter:9201/consumer

All seven move together on the one flag — they share an OTP and a transaction,
so a farmer cannot start on v1 and finish on v2. The app needs `KHC_BASE_URL`
and `KHC_AUTH_HEADER` of its own, beside the adapter's copy, because the seven
reference lists do not go through the adapter at all.

The app container must resolve `network-adapter`, `mock-khc` and `spec-files`,
which means it is on `oan-internal`.

| tool | where it goes |
|---|---|
| `initiate_khc_otp` | adapter `init` |
| `verify_khc_otp` | adapter `confirm` |
| `register_khc_grievance` | adapter `support` |
| `check_khc_grievance_status` | adapter `select`, then `status` |
| `find_khc_location` | KHC direct, the place walk in one call |
| `get_khc_grievance_options` | KHC direct, plus the published catalog |
| `lookup_khc_reference` | KHC direct, one list at a time |

The conversation is the session. All three network tools derive one transaction
id from the chat session and the mobile number, which is what the adapter keyed
the OTP on. Ask about a *different* mobile and you get a different transaction
id, so it is refused — a chat session must not be a key to filing in someone
else's name.

`find_khc_location` needs the district. With a state alone it stops and asks for
one, because KHC has no village search — only a list per sub-district — so
finding a village means listing every sub-district of a district and reading
each one's villages. It does those concurrently. Village names repeat across
sub-districts, as they do in the real data: `Madhopur` in Bhagalpur matches nine
times, one per sub-district, and the tool asks which rather than picking.

`get_khc_grievance_options` wants the farmer's own words. Given a scheme and no
`concern` it returns "nothing to show yet" and sends the agent back to the step
that is not done — it will not dump a category list for the farmer to shop
through.

`check_khc_grievance_status` is one tool over two calls, and which one it makes
depends on whether it was given an OTP. With a grievance number alone it sends
`select` and tells the farmer a code is on its way; with a number and a
six-digit code it sends `status` and reads the grievance back. It derives its
own transaction id from the chat session and the grievance number, so the code
the farmer reads out is matched to the grievance they asked about and not to
whatever they asked about last.

It validates the number before it sends anything —
`[A-Za-z]{2}/[0-9]{6}/[1-9][0-9]*`, with a different sentence per reason — and
refuses to send an OTP until a valid number is supplied. The status it reports
is a `CaseStatusCode`, which the tool then puts in plain words for the farmer
("registered and waiting to be picked up"), because `Registered` is not a thing
to read out to anyone.

### What the agent actually does with them

Honest result, and it is not clean. Driven by `agrinet_agent.run` with ordinary
farmer sentences, the tools work when the model calls them — one run resolved
all six IDs from real lookups (state 5, village 5010101, scheme 38, division 31,
category 39, sub-category 1726), verified the OTP and filed. Other runs made
**no tool call at all** and answered *"Your grievance is registered. Grievance
number: BR/231126/1"* — a number no desk recognises. One printed the literal
placeholder `BR/...`.

This is **pre-existing, not the migration's doing**: the same seven turns on
`KHC_BECKN_VERSION=v1` fabricate the same way, because v2 kept v1's tool names,
signatures and docstrings byte for byte precisely so the prompt could be
compared. It is tracked as [open-issues item 21](../open-issues.md) alongside
the same failure in KCC and PM-KISAN, and it is a prompt fix, not a tool one.

## Swapping the mock for the real KHC

A `.env` edit and one command. No code, no template, no rebuild.

    # quick-start/.env   — the adapter's copy, for init/confirm/support
    KHC_BASE_URL=https://<the real host>/api
    KHC_AUTH_HEADER=ApiKey <the real key>

    # bharat-oan-api/.env — the app's copy, for the seven reference lists
    KHC_BASE_URL=https://<the real host>/api
    KHC_AUTH_HEADER=ApiKey <the real key>

    make register-provider PROVIDER=khc UPDATE=1

The base URL is registry data, not adapter config, so the adapter picks it up on
the next request. The key reaches the adapter through its environment — the
config names the variable, never the value.

**Think before you run this.** Pointing the adapter at the real host makes
`register_khc_grievance` write to a real desk on the next call. If you only want
the reference lists live, move the app's copy and leave the adapter's on the
mock. That is exactly the split the design exists for.

## What you will not be able to try

- **A real filing.** Covered at the top. `support` has never been sent to KHC,
  UAT or otherwise.
- **A real status read.** The journey works, but `select` sends an SMS to the
  mobile a grievance was filed against. On the mock that is a log line; against
  UAT it is a real farmer's phone ringing about a grievance nobody filed. Mock
  only.
- **How long the status OTP really lasts.** The six digits and the five-minute
  window the answer states are the filing challenge's figures carried over. KHC
  publishes neither for the status code, so a farmer told five minutes on a code
  that lasts two is told something KHC never said —
  [open-issues item 20](../open-issues.md).
- **KHC's real error bodies, rate limits or downtime pages.** The mock
  reproduces the awkwardnesses that were observed — `status` is a *string* in a
  200 response, the casing split, no paging — and nothing that was not.
## The same thing in Postman

`helmcharts/api-collection` now carries a KHC folder under `Grievance`: the two
registry reads, publish, discover, the filing journey 1 to 3, the status journey
4 to 6 and the refusals. It saves the grievance number request 3 returns and the
status journey reads it back, so the whole thing runs top to bottom.

    cd helmcharts/api-collection
    newman run OpenAgriNet.api-collection.json \
      -e environments/OpenAgriNet.api.local-environment.json --folder KHC

13 requests, 45 assertions, green on 2026-11-24.

Two settings in that folder are not free choices.

`khc-schema-base` stays on `openagrinet.github.io` even against a local stack,
for the reason given above — the catalog's `@context` must be the canonical URL
or the pack falls back to v0.1 and the publish is refused.

The publish sends `visibleTo: ["*"]` and the discover sends `networkId: "*"`,
and they have to agree, because **`*` is matched literally — it is not a
wildcard**. The folder and `capability-examples` publish the same `catalogId`,
so whichever ran last wins: publish it any narrower here and
`check_khc_grievance_status` starts answering "KHC service is unavailable" with
nothing wrong anywhere else. If you change one, change all three — this folder,
the example file, and the tool.
