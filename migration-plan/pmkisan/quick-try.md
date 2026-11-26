# PM-KISAN on v2 — try it yourself

Fifteen minutes, no AI in the path for the first half. Everything below ran
green on 2026-11-20 against `mockpmkisan`. **Nothing here has been run against
the real PM-KISAN service.**

## Before you start

    cd helmcharts/quick-start
    make up
    make check-mappings          # all five PM-KISAN mappings must resolve

If a mapping URL does not resolve, a `select` later fails with a
`NET_INTERNAL_ERROR` that does not name the URL, and you will waste an hour.

The adapter is at `localhost:9201` from the host, `http://network-adapter:9201`
from inside a container. v1 posted to the BAP at `/init`, `/status`, `/search`;
v2 posts to the **consumer adapter** at `/consumer/init`, `/consumer/status`,
`/consumer/support`.

The mock's OTP is `1234` (`MOCKPMKISAN_OTP`). Registration `UNKNOWN0000`
(`MOCKPMKISAN_UNKNOWN_REG_NO`) is the one it has never heard of.

## Half 1 — the network path, by hand

Copy the shipped bodies and change only the ids:

    helmcharts/quick-start/capability-examples/Grievance/        init, support, status
    helmcharts/quick-start/capability-examples/SchemeApplication/  init, status

Each has a README. All five were sent at a live adapter on 2026-11-20 and
answered. One of them needs an edit before it will work: `Grievance/status`
carries `case.filedOn` as the literal placeholder
`REPLACE-WITH-THE-filedOn-support-RETURNED`, and you must put in the date
`support` actually returned. A static file cannot hold a date that has not
happened yet, and that is the whole trap described below.

Two things about the envelope cost an afternoon each when they were worked out,
so take them on trust:

- `message.contract.commitments[0]` **must** carry
  `status: {descriptor: {code: ...}}` — `DRAFT` for `init`, `ACTIVE` for
  `status`. Omit it and you get `SCH_REQUIRED_FIELD_MISSING`.
- `provider` lives on `offer.provider`. Putting it inside
  `commitmentAttributes` gives `SCH_FIELD_NOT_ALLOWED`.
- `support` is the odd one out: it uses `message.support.channels[0]` instead
  of a contract, and *that* object does carry `provider` at the top level.

Run the two journeys in order. **Chain them — do not replay fixtures.** A
fixture's `filedOn` is from whenever it was written, so the status read lands
on the not-found path by accident and the real path never runs.

1. grievance: `init` → OTP `1234` → `support` → `status`, carrying the
   `filedOn` that `support` returned into `status`
2. application status: `init` → OTP `1234` → `status`

One round-trip fewer than v1. v1 asked for an OTP, verified it in a second
call, then fetched in a third. v2 carries the OTP **inside the working call**
as `challenge.response`, and the provider adapter verifies before fetching.

### Reading the errors

| prefix | whose fault | what to do |
|---|---|---|
| `BIZ_*` | PM-KISAN's own answer | pass it to the farmer, it is written to be read aloud |
| `SCH_*` | ours — the request was built wrong | fix the request; the farmer can do nothing |
| `NET_DOWNSTREAM_UNAVAILABLE` | upstream is down | retry later |

A refusal only counts as a real refusal if it is `BIZ_*`. A test that asserts
"the call failed" passes on a malformed payload and proves nothing.

## Half 2 — the tools

    PMKISAN_BECKN_VERSION=v2      # default is v1

Same function names and signatures as v1, **with one exception**:
`pmkisan_grievance_status` takes `filed_on` (DD-MM-YYYY) on v2, because v2
reads one grievance rather than listing them all. Ask the farmer for it; do
not guess it.

Check the flag actually took effect before trusting a pass:

    from agents.tools import pmkisan_grievance_status
    assert pmkisan_grievance_status.__module__.startswith("agents.tools.v2.")

A silently-missed flag otherwise looks exactly like a green run on v2.

### Registration numbers

Two different ones, for a reason. `UP123456789` passes the tool's validator
(`^[A-Z]{2}[0-9]{9}$`). `UP12345678A` is what the api-collection's fixtures
use, and it is **rejected** by the tool — deliberately; see
`tests/test_pmkisan_registration_validation.py`. Use the first for anything
going through a tool, the second only for raw adapter calls.

## What you will not be able to try

- **Lodging a grievance.** Disabled in production and still disabled here.
  Thirteen of the twenty grievance types have no v2 category anyway — see
  `plan.md`, finding 1.
- **The real encryption parameters.** Key length, IV handling and padding are
  our reading of the provider's scheme, and the mock agrees with us by
  construction. This is the one thing a mock cannot catch.
- **The OTP validity window.** `grievance.init.yaml:28` hardcodes ten minutes
  and reports it to the farmer as fact. Nothing PM-KISAN sends says ten
  minutes. That number is ours.
