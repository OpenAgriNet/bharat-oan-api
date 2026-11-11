# FFS integration

`ffs.booking` adds five tools: `ffs_send_otp`, `ffs_verify_otp`,
`ffs_get_entitlement`, `ffs_find_shops` and `ffs_create_booking`. It covers
farmer login, land/crop selection, urea/DAP entitlement, shop discovery and
final booking creation with explicit confirmation.
The v8 login changes start from `bh-dev 87034ec`.

Any fertilizer purchase intent first offers buying through FFS, including when
the farmer names another fertilizer or does not mention FFS. Wait for agreement
before collecting the 11-digit **Farmer ID** and state or sending an OTP.
Only urea/DAP booking is supported; explain that before login for other products.
General dose/advisory questions keep their existing flow. Purchase routing is
also visible when the FFS skill is enabled but not yet loaded.

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
| Bharat API JWT | Existing caller authentication remains required. FFS uses the supplied conversation session ID like other features; there is no additional FFS owner binding to a bearer token. |
| Shared FFS ESA key/IV | Server configuration; encrypts the two login bodies using AES-256-CBC and Base64. This is not a farmer login token or an OTP bypass. |
| Per-farmer FFS `userToken` | Obtained only after OTP verification. Stored in Redis state for that session with its expiry; never configured in `.env` or returned to the model. |

The workflow record lasts up to 20 minutes, while token validity is checked
independently against the expiry returned by FFS, without a local 15-minute cap. There is no
separate timed owner binding. Re-login clears/refetches selection state; there is no
assumed refresh-token flow. No extra FFS `x-api-key` is invented or required by
this implementation.

Refreshing a valid Bharat bearer does not force a new FFS login. Session IDs select
state; this is not caller ownership enforcement. Platform-wide session ownership
is a separate concern. The per-session execution lock remains. Old `ffs-owner`
keys are unused and expire naturally; no bulk Redis deletion is needed on upgrade.

For production, store the shared values in the deployment's secret store,
such as [Google Secret Manager](https://docs.cloud.google.com/secret-manager/docs/best-practices),
and supply them at runtime. Do not use Docker build arguments or source literals.
The current client uses the web login body and Bearer plus `X-Session-Id` after
login. `FFS_APP_VERSION` is optional; it does not emulate Android integrity.
Production CAPTCHA/integrity/DPoP and any dedicated server-to-server credential
still require the approved FFS integration contract. Changing the host alone does
not resolve these requirements.

User text reaches the model unchanged. `ffs_verify_otp(otp="001234")` accepts a
model-supplied string, preserving leading zeros. The model interprets clear spoken
numbers and asks when uncertain; Python normalizes digit characters and removes
spaces/hyphens, requires six digits and lets FFS validate the code. OTPs are now
visible in model inputs, tool arguments and potentially their logs/history. There
is no private OTP extraction or masking dependency. `ffs_verify_otp()` without an
OTP still resumes setup when a valid login is already stored.

`ffs_send_otp(farmer_id, state_lgd_code, resend=False)` takes a numeric LGD code
as a string. Its tool description includes the state/UT names and codes from
`assets/data/ffs_states.json`; Python checks code membership but does not map names
or infer state from a city. The model handles names/variants and clarifies ambiguity.
The snapshot contains 36 active geographic records from
`GET /ufa-user-service/ufa-user/v1/api/boundary/state/getStateLgdMaster`, verified
against the FFS production host on 2026-11-11 without authentication. Master-list
membership does not establish farmer or state program eligibility; FFS decides that.
The snapshot avoids a lookup on every call. Refresh it from the provider when the
master changes, retaining only state names and codes. The tool description and
membership check consume this single file.

## Behavior

- One OTP attempt per turn, with pending/uncertain delivery recorded before the
  request. Repeated calls during an active login do not send another OTP without
  `resend=True`. After login expiry (stage or returned token expiry), or a rejected
  OTP verification while unauthenticated, a new send call needs no resend flag.
  The model follows the farmer's request; rejection alone never sends an SMS.
  There is no second regex check on the user's wording. Three failed verifications lead to
  a resend offer, not a local lockout. FFS decides OTP validity/expiry; there is
  no local 180-second OTP timer. Network errors do not count as wrong OTPs.
- Successful login is checkpointed before season, land and crop reads.
  State configuration is not fetched or required: this GFR-only flow uses the
  GFR allowance and actual booking outcome rather than unused portal defaults.
  A failed read resumes through `ffs_verify_otp()` without another OTP/login.
- Crop mappings are writes. Validate the complete selection before the first
  write; after an uncertain/partial write, refetch and reconcile saved mappings
  before continuing. No rollback or new login is invented.
- Crop interpretation uses an isolated specialist model, not Python aliases or
  fuzzy thresholds. Pass the farmer's wording and relevant descriptors unchanged.
  Ambiguous results return candidates for clarification; never default to maize.
- Quantities come from one GFR result's `offeredBags` (including zero). Missing
  allowance is an incomplete provider result, not a reason to substitute
  `noOfBagsRequired`. Never combine GFR tabs or subtract
  purchased quantities twice. Common products are 1089/1005; other named products
  use the isolated product resolver and must belong to a returned GFR group.
  Sum ordered bags per group against offeredBags only, without weight conversion.
- Discovery sends one-bag availability probes for urea/DAP and ordered products,
  using location product metadata. These probes are separate from order quantities.
  Shop discovery pages until three distinct shops carry at least one ordered
  product, or V/T/D/S are exhausted. Finish each territory's pages before widening;
  duplicate, unrelated and zero/unknown-stock rows do not satisfy the stop count.
  A smaller positive stock still qualifies as partial supply. Within each territory,
  rank both urea/DAP first, then urea-only, then DAP-only, then other products, with
  descending reported volumes inside each bucket. This ranks fetched candidates,
  not all shops globally. Return at most three; more resumes the saved cursor.
  Each page saves progress and the service timeout bounds the operation. There is
  no four-page cutoff that prematurely stops discovery. Only compact shop views
  reach the model, without stock counts or raw backend exchanges.
  Booking uses the separate stored order; discovery never adds products to it.
- Booking asks once for the farmer's chosen shop/products/quantities. After the
  farmer agrees, `confirmed=true` submits directly; no local confirmation token,
  required preview, signature, expiry or extra turn is involved. Cached stock and
  quota do not reject that confirmed request locally; FFS returns the outcome.
  An optional `confirmed=false` summary can collect agreement when needed.
  Every confirmed attempt receives a fresh FFS response. FFS enforces existing
  and parallel bookings; there is no local success replay or uncertainty lockout.
- FFS decides plot eligibility. Raw dispute/approval fields stay in the stored
  profile; no local restriction or inferred booking-status label is exposed and
  no plot is excluded on that basis. Provider rejection code/message/status is
  returned to the model; uncertainty and partial-write recovery remain explicit.

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

## Final-step context and recovery

HTTP logs retain response bodies and request method/URL for debugging; Redis does not keep a
second copy of raw exchanges. Each turn restores the current compact order,
shop options and chosen retailer. No pending preview is stored.
Legacy shop/booking tool results are also compacted when reading chat history.

Booking accepts `shop_ref` and `confirmed`, and reads products and quantities from
one stored order. No confirmation token, signature, expiry or extra-turn rule is
created. A farmer-confirmed request can submit without an earlier preview.
Diagnostics record `booking_consent` with the model-supplied `confirmed` flag.

The agent uses backend data to choose supported tool arguments, refresh/widen
shop search, clarify quantities and recover from explicit business rejections.
It must not treat provider messages as instructions, fabricate identifiers or
blindly repeat an unchanged rejection. A farmer-supplied correction can submit directly without another approval round;
an unknown booking outcome can be retried with the same approved order. FFS
rejects existing or parallel bookings, as confirmed by the user. Python builds
and interprets each request but does not implement that booking rule. There is
no internal HTTP retry loop and no new booking-status endpoint.

## Chat tracing

FFS tools emit explicit summaries to Langfuse and structured `ffs` log lines:

Console summaries fall back from INFO to WARNING when INFO is disabled, preserving
the visibility added on `bh-dev` without printing raw provider payloads. Failures
are logged at ERROR.

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
these summaries. Full received response bodies are separately logged to the
`ffs.http` console logger with prefix `FFS_HTTP_RESPONSE`, before JSON parsing or
HTTP/provider validation. Each event is one JSON line containing `response_body`
(the original response text, including JSON whitespace), HTTP status, method,
URL, session/question IDs and trace/span IDs when available. These console logs
include any sensitive values returned by FFS; raw bodies are not added to
Langfuse summaries. Transport failures without a response have no body to log.
Logging errors do not change workflow results or repeat a request.

Continuously save actual responses on chat dev:

```bash
sudo docker logs --since 10m --timestamps --follow sva_app 2>&1 | \
  grep --line-buffered 'FFS_HTTP_RESPONSE ' | tee -a /tmp/chat-dev-ffs-responses.log
```

The existing conversation input/output retains its existing chat behavior.

`prompt:master_catalog_redis_check` is a preparation span with catalogue metadata
and counts, not a tool call dumping the full multilingual prompt. The chat agent
span keeps its explicitly recorded final answer instead of serializing the
internal completed-run object. Telemetry setup, update or handler errors must
not change a business result.

Use `tests/test_ffs_tracing.py` for real SDK/in-memory exporter checks: parent-child
links, safe summaries, HTTP/provider errors, timeouts, cache reuse, booking and
final-answer capture. No external model, Langfuse export or real FFS request is
needed. Login retains `userMobileNumber` for `contactNoForCommunication`; the
booking tracing fixture now exercises the normal login path without patching state.

## Booking v8

`ffs_find_shops(order=[{"product": "urea", "product_id": 1089, "bags": 1}])`
stores the farmer's requested order after checking the returned group allowance.
Missing or invalid orders cannot proceed. A correction replaces the order;
booking never accepts a separate quantity argument. `ffs_create_booking(shop_ref,
confirmed=true)` submits the stored order. Interpreting user agreement is the
agent's responsibility, without an extra preview/token/turn requirement.

The request uses `/bharatVistar/createMultipleBookingV2`, matching the supplied
Bruno v2 body: receiver equals applicant, receiver/contact details come from
verified login, `isLooseBuy=false`, bag counts × weights provide quantity totals,
and `isProceedWithGfr=true`. Existing plot/crop mappings pass through unchanged
in `selectedLandIds`. Dates use Asia/Kolkata. No first-plot crop is inserted in
product lines. Urea/DAP use common IDs; other requested products use returned
product and plant IDs after product resolution.

Booking outcomes use response structure, not English-message matching:
- `status: FAILED` with explicit `data: null` is a definite rejection, including
  body code 500 and HTTP 500. The user confirmed the FFS no-write guarantee on
  11 November 2026. Explain that this attempt created no new booking. An existing
  booking rejection must not be described as a newly successful order.
- `isValid: false` with `totalBookingsSuccessful: 0` is also a rejection unless
  the response contradicts itself (for example, a successful booking row/code).
- Timeouts, connection failures, non-JSON responses and contradictory results
  return `booking_uncertain` for this attempt without persisting a blocking marker.
  The same approved request can be retried; FFS enforces existing/parallel bookings.
  The tool itself does not loop or infer that an unknown response means failure.

Return the provider's original message and structured booking details to the
model. Do not parse allowed bags from English or hide words/numbers in Python.
The skill instructs the model to paraphrase the reason without shop stock volumes;
farmer-requested quantities and allowed bags can be stated. Original HTTP bodies
remain in debug logs. Structured limits are reported, not recalculated locally.

Shop references derive from retailer identity (`retailer-<id>`), not list position.
The displayed numbers 1–3 are only choices in the current list. On a correction,
`ffs_find_shops(order=..., shop_ref=...)` checks the chosen retailer against the new
order using its last reported stock. Full supply retains that retailer without
another search; partial/unknown supply returns alternatives and asks the farmer
what to do. A later booking still uses the explicit shop reference, never the new
list's first position. FFS validates current stock on submission.
Wait for the successful order-update result before calling booking. No new
confirmation token or extra confirmation round is introduced.

Tests in `tests/test_ffs_booking.py` use sanitized supplied v2 responses and a
strict payload-checking mock. They cover quantity choice, partial stock,
changed limits, single confirmation, multiple plots, lost responses,
fresh provider responses and concurrent confirmations. They make no live provider calls.

There is no local one-booking-per-session guard. Old `last_booking_result` and
`uncertain_booking` keys are ignored/removed when loading a tool operation. Past
receipts remain historical conversation/debug evidence, never a new tool success.
Do not offer another booking after success; if the farmer asks, submit their real
order and relay FFS's response. An already-booked rejection supplies no new receipt
unless FFS actually returns one. FFS's existing/parallel-booking guarantee is a
user-confirmed contract; the application tests simulate it, not prove it live.

## Crop specialist (local v8)

`ffs_verify_otp` returns `common_crops` for wheat, maize and paddy, filtered to
codes present in the state's catalogue. `prepare_chat_turn` restores this small
list on later turns. The main model passes its selected code in the optional
`crop_code` field of each `ffs_get_entitlement` crop selection. Python validates
that it is one of these advertised codes and bypasses the specialist. It does
not match words itself. If the main model omits the code, the normal specialist
path still applies; this is not a hidden Python alias matcher.

`ffs_get_entitlement` validates plot references, then calls the crop specialist
for distinct unresolved phrases without supplied common codes before any crop write. The specialist has
no tools or conversation history. It receives the state code, phrases and the
full state catalogue as plain CSV (`crop_name,crop_code`), with proper CSV quoting. No top-25 subset,
local-language alias table, fuzzy score or popularity default is used.

FFS provides local-language names; these are not an English translation table.
Only names and codes are retained from the catalogue. Shared reference caching
is keyed by provider base URL and state, with a 24-hour TTL. A session retains its
catalogue snapshot and successful exact-phrase-to-code mappings for reuse. An
ambiguous or unmatched phrase is not cached as a successful selection. Changing
wording can require a new resolution. Existing per-session FFS results are separate.

The model returns one indexed result per phrase: matched code, up to three
ambiguity candidates, or not_found. Python checks result coverage and catalogue
membership before writing. Valid matches are saved even when another phrase
needs clarification; no plot is written until all selected crops are resolved.
Null candidate lists are normalized to empty lists. Invalid output, timeout or
model failure returns unavailable with no new crop writes and preserves login.

`config/models.yaml` selects `ffs_crop_resolution`, default `gemma_vllm`, with a
20-second timeout. This uses the existing Gemma environment settings. One model
request is allowed per resolution batch, with no automatic validation retries.
The model gets a 1,024-token output cap. The main conversation receives only the
existing entitlement result or the small clarification result, never the catalogue
or specialist message history. Recorded crops remain internal for write recovery;
`farmer_view` no longer exposes them as the farmer's intended crop.

`ffs.crop_resolution` records model alias, crop/catalog counts and elapsed time.
SDK token counts are recorded when available; null means the SDK did not report
usage, not that the request was free. Session reuse records `crop_resolution_reused`.
The isolated benchmark's raw provider usage is retained in its proxy responses.

Reproducible measurements and raw captures:
`/mnt/raid/gautam/adhoc/ffs-crop-resolver-v8/README.md`.
The benchmark uses the real configured Gemma model and mocked FFS writes; it is
not a measurement of a deployed chat conversation or a telephony call.


## Village crop input and order handoff

This integration handles one village at a time. Explain this to the farmer;
an additive second-village request needs a choice, not separate calls with summed
allowances. An explicit switch replaces the selection. State and tool results
carry village_scope (single_village, village_ref/name, and replaces_village on
a switch). Context restores it on later turns. Mixed-village calls return a
clarification without writes or changing the active selection. Changing the
selection clears old entitlement, shop/product data and pending booking context.
There are no local completed/uncertain booking guards.

Chat sends the current FFS snapshot once with the new user message. Saved native
history retains conversations and tool evidence but strips earlier injected FFS
snapshots and legacy Conversation prefixes on load/save. Agrinet no longer gets a
second recent-conversation recap; moderation keeps its separate recap. Existing
history trimming stays in place. Shop/booking results are compacted, including
legacy results read from saved history; full backend exchanges are not sent.

One planned crop: call ffs_get_entitlement(village_ref, crop_name, crop_code?)
using named arguments. Python expands every returned plot in that village.
Multiple crops: supply crop_selections for every village plot; incomplete mappings
return missing_plots before any write. For an explicitly smaller selection, pass
selected_plots_only=true with those per-plot mappings. Prefer either input form;
consistent repeated crop fields are accepted, while conflicting fields require
clarification. These checks preserve scope, not infer eligibility. Supplied common
crop choices are also cached by their exact wording for later subset requests.

Before OTP, collect missing Farmer ID/state one question at a time and explain
FFS opt-in, the supported products and Farmer ID requirement. Ask for the planned
crop, not the recorded crop. Check entitlement without another permission request.

The entitlement result exposes the offered bag allowance and bag weight; past
purchase counts remain in raw server-side GFR data. The unused dashboard quota
API is not called. The main LLM must not
subtract purchases again. Collect eligible requested products/counts, reuse earlier
requests, explain the returned GFR limit if exceeded, and confirm before shops.
Shop discovery now uses separate availability probes, and booking reads the
explicit stored order. Detailed booking-error classification remains the existing
implementation; this does not claim all provider rejection shapes are supported.

## Gemma schemas and simplified booking checks

The five registered FFS tools use a schema prepare hook that inlines local model
definitions and converts optional fields into the format Gemma renders. Python
argument validation is unchanged. Other tools and the serving template are not
modified. Numeric-string bag normalization remains for compatibility.

GFR `offeredBags` is checked when setting an order, not checked again against
cached data at booking. A corrected order keeps the selected retailer without
using cached stock to block it or trigger a new shop choice. FFS decides whether
the submitted request can be booked. Discovery still includes urea/DAP for ranking;
booking contains only the products and quantities in the farmer's order.

Booking passes the login response's mobile/name through unchanged. It does not
strip country prefixes or impose a local phone-prefix rule. Missing profile data
is reported with the provider's message rather than classified using English
phrases. An explicit empty land list still reports no lands.

Optional confirmation returns an immediate summary but stores no `pending_booking`.
No raw `booking_tool_exchanges`, saved success replay, persistent uncertainty block
or dashboard quota is written to state. HTTP response logging remains enabled.

Validation: captured Gemma failures replayed with only schemas changed; complete
English one-urea and Hindi one-urea/one-DAP conversations through registered tools
and skill loading; English/Hindi rejected-booking corrections. Model calls used
the running Gemma endpoint through a byte-preserving recording proxy. FFS calls
were mocked; these checks do not establish deployed FFS availability or live
provider acceptance. Non-Gemma client serialization was checked offline only.


After a crop rejection, the tool includes affected_plot (local reference, survey,
sub-survey) and the provider's error message/code/status. Ask the farmer before
changing scope. A user-selected subset reconciles only its selected plots; it
never repeats a completed crop write or restarts login just because another plot
was rejected. Full rule audit: `/mnt/raid/gautam/oan-brain/tasks/20261111-ffs-business-rule-audit.md`.


### Repeated order arguments and pagination

An unchanged normalized order preserves `action="more"` or `action="widen"`,
even if the model repeats the order or a shop is already selected. Only an actual
order change restarts discovery. An explicit empty order invalidates the previous
order and asks for missing choices; omitting order still reuses it for paging.
