# bharat-oan-api

## PM-KISAN registration validation

Registration numbers contain two letters followed by nine digits (11 characters,
not 11 digits). Use only a dummy example such as `TN000000000` in user-facing
format guidance, never a real registration number.
Prompts trim surrounding whitespace and uppercase the letters before checking.
A valid format proceeds to the relevant tool; it does not establish that a
beneficiary exists. Portal results must come from the tool, not prompt validation.
The same rule applies to all supported language prompts and status tool descriptions.

Run the prompt regression check without application dependencies:

```sh
python3 -m unittest discover -s tests -p test_pmkisan_prompt_validation.py
```

With application and test dependencies installed, also run:

```sh
python3 -m pytest tests/test_pmkisan_registration_validation.py
```

## Callback endpoints

Use these endpoints for AgriStack callback integration:

- POST/GET callback receiver: /api/callback?from=agristack
- Optional wildcard receiver: /api/callback/<any-sub-path>?from=agristack
- Callback status check: /api/callback/status?from=agristack&callbackSessionId=<id>

Behavior:

- callbackSessionId is accepted from query param, body.callbackSessionId, body.sessionId, or body.data.sessionId.
- Callback receipt is stored in Redis namespace callback-status with TTL.
- Status endpoint returns received or not_found.
- farmerId and consent fields are extracted as metadata when present.

Optional env vars:

- CALLBACK_SESSION_TTL_SECONDS (default 3600)
- CALLBACK_AUDIT_DB_URL (Postgres URL, enables audit writes)
- CALLBACK_AUDIT_TABLE_NAME (default agristack_callback_audit)


## Delete all Docker volumes (cleanup)
```
docker system prune -a --volumes
```

----

## Create a Docker network
```
docker network create oan-network
```

## Run Redis (as a separate service)
```
docker run -d --name redis-stack --network oan-network -p 6379:6379 -p 8001:8001 redis/redis-stack:latest
```

## Docker Compose: Build and Run the API
```
docker compose up --build --force-recreate --detach
```

## Stop All Services
```
docker compose down --remove-orphans
```

## View Logs
```
docker logs -f oan_app
```
---

## Kisan Help Center (KHC)

The agent supports KHC grievance registration for new/existing farmers and
grievance status via the `khc-agri` Beckn provider. It uses the existing
`BAP_ENDPOINT`, `BAP_ID`, `BAP_URI`, `BPP_ID`, and `BPP_URI` configuration.
Only the provider backend needs `KHC_BASE_URL` and `KHC_API_KEY`.

KHC is intent-gated: the agent does not proactively suggest it. For an
unspecified grievance request, it may ask whether the user wants KHC and must
wait for explicit agreement before calling KHC. A grievance for another named
service stays on that service; unrelated questions do not trigger KHC suggestions.
An explicit registration/status request naming KHC, Kisan Help Center/Centre or
Kisan Helpline Center/Centre already satisfies this opt-in; do not ask it again.
Registration proceeds to collecting a missing mobile number or sending OTP to
the supplied mobile, while status proceeds to collecting a missing grievance
number or checking it. OTP verification and final submission confirmation are
still required for registration.

- **Registration:** `initiate_khc_otp` sends an OTP to the supplied mobile;
  `verify_khc_otp` verifies it and returns whether the farmer is registered.
  While awaiting OTP, a six-digit reply goes directly to verification using the
  same mobile; leading zeros are preserved and regional digits are normalized.
  The agent must not reject it or request it again without a verification result.
  Actual invalid/expired OTP responses and service failures are distinguished;
  language and other remaining details are collected only after verification succeeds.
  `lookup_khc_reference` resolves state/district/sub-district/village and
  scheme/division/category/subcategory IDs from KHC. After summarizing the
  grievance and obtaining explicit approval, `register_khc_grievance` submits it.
  New farmers supply name/gender; existing farmers do not need to repeat them.
  Registration accepts only OAN UI languages also accepted by the KHC provider:
  `hi`, `en`, `mr`, `bn`, `gu`, `kn`, `ta`, `te`, `as`, `ml`.
  UI language Maithili (`mai`) is not currently accepted by the KHC provider;
  the farmer must choose a supported grievance language explicitly.
  Registration normalizes these English language names and case/whitespace
  to KHC codes (for example, `Hindi` -> `hi`, `English` -> `en`), and new-farmer
  gender names to `M`/`F`/`O` (`male` -> `M`). Unknown languages/genders are still
  rejected without submission; no silent language fallback is used. Normalized
  values are used before deriving the duplicate-safe external reference.
- **Location reuse:** an explicitly stated grievance location takes priority.
  Reference name filters ignore case and whitespace (`uttarpradesh` matches
  `Uttar Pradesh`), preserving official names/IDs and ambiguous results.
  The agent does not demand spacing corrections; it resolves state before
  requesting district and asks one focused location question at a time.
  Otherwise, permitted browser coordinates already supplied by the UI are resolved
  using `reverse_geocode`, then matched through KHC's location reference hierarchy.
  If coordinates are absent or incomplete, or the geocoder cannot identify the
  village, the agent asks only for the missing state/village/district details.
  If the district cannot be established from the village details, the agent asks
  for it rather than guessing. After verifying the district in KHC, it fetches
  the district's sub-district list and presents bulleted API-backed choices
  (with further pages when available) if the sub-district is missing. Farmers
  select by name only from the list, not by number.
  The selected sub-district is used to fetch villages and match the village
  already supplied. Empty/failed lookups are explained, not replaced with guesses.
  Village and all other required IDs are resolved before final submission
  confirmation; already supplied/resolved location details are reused.
  The final summary confirms the resolved grievance location before submission.
  This does not require an AgriStack login.
- **Status:** ask for the grievance number, retaining slashes and leading zeros,
  then call `check_khc_grievance_status`. No mobile, OTP or cookie is needed.
- Verification is bound to the conversation/mobile and expires locally after
  five minutes. Changed mobiles, expiry and provider restarts require fresh OTP.
  Do not automatically retry OTP sends or registration after uncertain failures.
- Scheme IDs and division IDs come from the same reference row. Never guess IDs
  or apply sample IDs to a different farmer. Name-filtered lookups return 50
  options per page. Ask the farmer when matching entries are ambiguous.
- **Problem-based routing:** after resolving the state, the agent fetches its
  schemes and matches the farmer's complaint to returned scheme names, then
  resolves category/subcategory using their parent IDs. Farmers are not asked
  for scheme names or internal IDs upfront. A generic complaint that fits several
  schemes triggers a focused clarification, not an assumed scheme. The `name`
  filter is a literal substring, not semantic search; unfiltered options and
  further pages are checked when a keyword has no match. Already supplied details
  are reused instead of requesting a full registration questionnaire after OTP.
  AIF, A.I.F. and Agriculture Infrastructure Fund are treated as equivalent
  names when matching returned schemes; an already named AIF is not reconfirmed.
  Categories/sub-categories are matched by meaning, with API-backed names or a
  focused clarification if ambiguous. Empty filters trigger unfiltered lookup
  and pagination; API failures are not reported as unsupported schemes.
  Missing mappings block readiness claims and final confirmation: there is no
  category-only submission or invented sub-category fallback.
- Creation uses a stable external reference for the same conversation/details,
  supported by KHC's duplicate-reference semantics. Unconfirmed submissions are
  reported explicitly; do not claim success without a returned grievance number.

All language prompts include the shared KHC flow rules. OTP verification tool
input/output capture is disabled; no login tokens or cookies reach the model.

Run focused tests:
`python -m pytest tests/test_khc_tool.py`.

## AgriStack callback URLs

Use these public callback endpoints for AgriStack integration.

- Callback (POST, from AgriStack): `/api/callback?from=agristack`
- Status check (GET, from frontend): `/api/callback/status?from=agristack&callbackSessionId=<id>`

The callback response includes:
- `from` query param value
- all query params
- selected request headers
- parsed body (`json` / `form` / `raw`)

`callbackSessionId` behavior:
- When callback POST includes `callbackSessionId`, backend stores receipt status in cache.
- Frontend then checks status via `GET /api/callback/status` using the same `callbackSessionId`.

### AgriStack synchronous farmer data

The login callback must include a top-level `stateLgdCode` (for example, `22`).
It is retained in the cached callback and sent as a `stateLgdCode` Beckn item tag
alongside `farmerId` for Categories A, B and C, including seasonal fallback calls.
The provider forwards it as the mandatory `X-State-LGD-Code` gateway header and
maps `farmerId` to the gateway's `frCentralId` query parameter. Profile caches are
scoped by state as well as session, farmer, category and season.

Consent is still required. Missing or malformed state codes prevent data requests;
the farmer is prompted to log in again. Old cached callbacks without a state code
must be replaced by a new login. Response decryption and private-key configuration
belong only to the provider backend, not the browser or this chat API.

Run the relevant unit tests with:
`python -m pytest tests/test_agristack_tool.py tests/test_callback_router.py`.
