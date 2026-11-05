# FFS integration v0

`ffs.booking` adds five tools: `ffs_send_otp`, `ffs_verify_otp`,
`ffs_get_entitlement`, `ffs_find_shops` and `ffs_create_booking`. It covers
farmer login, land/crop selection, urea/DAP entitlement, shop discovery and
final booking creation with explicit confirmation.
The branch starts from `bh-dev b263117` after the skill-breakdown-v2 merge.

## Configuration and authentication

Use a **server-side** environment file for local development. On this workspace,
the prepared mock configuration is `/mnt/raid/gautam/envs/ffs-integration-v0.env`
with permissions `0600`, outside the repository. It points to the local FFS mock
on 18140 and a dedicated test Redis on 6401. Do not commit or bake this file into
an image. `.env.example` contains empty placeholders only; Git and Docker ignore
environment files. Load it into the process environment, or supply it with the
container's `--env-file`; the application does not automatically discover that
external path.

```dotenv
FFS_BASE_URL=http://127.0.0.1:18140
FFS_ESA_KEY=
FFS_ESA_IV=
FFS_APP_VERSION=
```

Fill the key and IV with the exact values supplied by FFS: 32 UTF-8 bytes and
16 UTF-8 bytes respectively. Settings use `SecretStr`. Leaving the base URL blank
makes FFS calls unavailable. Use the host address reachable by your container;
loopback inside a bridged container refers to that container.

There are three independent authentication layers:

| Layer | Storage/use |
|---|---|
| Bharat API JWT | Existing caller authentication remains required. FFS state/history access bind to a digest of the verified bearer plus session, never public `user_id` or guest `sub` alone. New guest tokens include a random `jti`. |
| Shared FFS ESA key/IV | Server configuration; encrypts the two login bodies using AES-256-CBC and Base64. This is not a farmer login token or an OTP bypass. |
| Per-farmer FFS `userToken` | Obtained only after OTP verification. Stored in private Redis session state with its expiry; never configured in `.env`, returned to the model, or reused across callers. |

The workflow record lasts up to 20 minutes, while token validity is checked
independently against the returned expiry, capped at 15 minutes. The ownership
binding lasts as long as conversation history and remains enforced if FFS is
subsequently disabled. Re-login clears/refetches selection state; there is no
assumed refresh-token flow. No extra FFS `x-api-key` is invented or required by
this implementation.

Keep the same Bharat bearer during an FFS conversation. A replacement bearer must
start a new chat session and FFS login; migrating an authenticated FFS session to
a refreshed token is not implemented in v0. This prevents someone minting another
guest token with the same public subject/fingerprint from reading the FFS session.

For production, store the shared values in the deployment's secret store,
such as [Google Secret Manager](https://docs.cloud.google.com/secret-manager/docs/best-practices),
and supply them at runtime. Do not use Docker build arguments or source literals.
The current client uses the web login body and Bearer plus `X-Session-Id` after
login. `FFS_APP_VERSION` is optional; it does not emulate Android integrity.
Production CAPTCHA/integrity/DPoP and any dedicated server-to-server credential
still require the approved FFS integration contract. Changing the host alone does
not resolve these requirements.

OTP entry is recognized only within an active FFS authentication/setup flow.
The chat ingress normalizes native digits and replaces the code with
`[FFS OTP supplied]` before application logging, telemetry and model input.
`ffs_verify_otp()` receives it through a private `SecretStr`, with no model OTP
argument. It does not enter saved model/tool history. Because chat uses GET,
deployment access logs must also omit/redact query strings; application redaction
cannot remove a query string already logged by a proxy or ASGI access logger.

## Behavior

- One OTP attempt per turn, with pending/uncertain delivery recorded before the
  request. Repeated calls do not send another OTP. Resends require an explicit
  farmer request; three failed verifications lead to a resend offer.
- Successful login is checkpointed before config, season, land and crop reads.
  A failed read resumes through `ffs_verify_otp()` without another OTP/login.
- Crop mappings are writes. Validate the complete selection before the first
  write; after an uncertain/partial write, refetch and reconcile saved mappings
  before continuing. No rollback or new login is invented.
- Maize has preference among plausible aliases/near matches. Clear other crops
  win; unresolved or duplicate registry names trigger clarification. Known common
  crop aliases supplement exact state-registry names; this is not a universal
  multilingual crop dictionary.
- Quantities come from one GFR result's `offeredBags` (including zero), with the
  documented `noOfBagsRequired` fallback. Never combine GFR tabs or subtract
  purchased quantities twice. The selected products are 1089/1005 only.
- Shops use V/T/D/S precedence, enough-stock checks, complete supply first within
  each territory, then urea-only/DAP-only. Page/time budgets report incomplete
  coverage honestly. More/widen preserve a cursor and deduplicate presented shops;
  widening keeps subsequent results restricted to complete supply in wider areas.
- Booking requires two-step confirmation through `ffs_create_booking`: first a
  summary/token response, then an explicit confirmed submission with the same
  token. If submission fails after dispatch, the status is `booking_uncertain`
  and retries must not be automatic.
- The supplied land response lacks a verified booked-land signal. It is shown
  as unknown, not inferred from quota. Disputed/unconfirmed approval records are
  restricted; no claim is made that unknown plots are bookable.

## Validation

Offline checks use Pydantic AI **1.32.0**, matching this branch. Tests exercise
the five tools, selective skill loading, all 13 language assets, OTP privacy and
caller isolation, setup recovery, partial crop writes, expiry, quantities and shop
ranking/paging. A deterministic Pydantic model drives the multi-turn tool loop;
this verifies orchestration, not live model answer quality.

The opt-in HTTP test first requires `/health` and `X-FFS-Mock: true`, with live
forwarding and booking disabled. It then tests the running fixture API, optionally
using a real dedicated Redis instance:

```bash
set -a
source /mnt/raid/gautam/envs/ffs-integration-v0.env
set +a
FFS_MOCK_TEST_URL=http://127.0.0.1:18140 FFS_REDIS_TEST_PORT=6401 \
  python -m pytest -q tests/test_ffs.py
```

Use the pinned test environment at
`/mnt/raid/gautam/python_envs/ffs-integration-v0-checks/bin/python` here. Without
`FFS_MOCK_TEST_URL`, the external HTTP test is skipped and tests use an isolated
transport. **Do not point this test at real FFS.**
No additional live OTP, production model request, or live booking is part of v0
validation. Fixed GFR and synthetic retailer territories cannot prove real
multi-plot calculations/geography. Live multilingual Gemma checks remain separate.

## OTP-send request headers

The initial `/authenticate/user/loginByFarmerId` request explicitly sends
`isForMobile: false`, `appType: APPLICANT`, and `platform: ANDROID`, alongside
Content-Type and the session ID. The `version` header uses `FFS_APP_VERSION`
when configured; it is omitted when blank. On current `bh-dev`, the encrypted
body has the state code and farmer ID; `isForMobile` is a header only.
Verification and subsequent request headers are unchanged.
This does not request the mobile CAPTCHA exemption or generate a reCAPTCHA token.

## Chat tracing

FFS tools emit explicit summaries to Langfuse and structured `ffs` log lines:

```text
agent.vistaar                         final answer, not internal AgentRunResult
  tool:ffs_get_entitlement            status, stages, decisions, quantities
    ffs.api:/applicant/land/crop/add   endpoint, duration, HTTP/provider status
    ffs.api:/...                      one span per actual HTTP request
```

All five FFS tools use the same tracing wrapper. Each tool/API event carries
session and question IDs; console events also include trace/span IDs when
available. Tool completion reports lock wait, API count, stage before/after,
cache reuse, OTP suppression, crop reconciliation, quantity source, shop search
territory/page counts and booking confirmation/submission decisions where relevant.
An API span records method, host/path, encryption, public application headers,
authorization/CAPTCHA presence, HTTP status, numeric provider code and a fixed
failure category. Exception **classes**, not messages, identify transport/state
failures. A cached result has no new API spans and includes a reuse decision.

Request/response bodies, OTPs, tokens, farmer identifiers, names, phone numbers,
land registry IDs and booking confirmation/reference codes are excluded from
these new summaries. Do not restore raw body logging for debugging. This is not
a claim that the existing conversation input/output is redacted: those fields
retain the application's existing chat behavior.

`prompt:master_catalog_redis_check` is a preparation span with catalogue metadata
and counts, not a tool call dumping the full multilingual prompt. The chat agent
span keeps its explicitly recorded final answer instead of serializing the
internal completed-run object. Telemetry setup, update or handler errors must
not change a business result.

Use `tests/test_ffs_tracing.py` for real SDK/in-memory exporter checks: parent-child
links, safe summaries, HTTP/provider errors, timeouts, cache reuse, booking and
final-answer capture. No external model, Langfuse export or real FFS request is
needed. The existing `bh-dev` booking test currently fails because login does not
retain `userMobileNumber`, while booking requires it. The tracing booking fixture
supplies that missing field to test telemetry separately; this logging change
does not repair or conceal the existing business-flow failure.
