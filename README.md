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

- **Registration:** `initiate_khc_otp` sends an OTP to the supplied mobile;
  `verify_khc_otp` verifies it and returns whether the farmer is registered.
  `lookup_khc_reference` resolves state/district/sub-district/village and
  scheme/division/category/subcategory IDs from KHC. After summarizing the
  grievance and obtaining explicit approval, `register_khc_grievance` submits it.
  New farmers supply name/gender; existing farmers do not need to repeat them.
- **Location reuse:** an explicitly stated grievance location takes priority.
  Otherwise, permitted browser coordinates already supplied by the UI are resolved
  using `reverse_geocode`, then matched through KHC's location reference hierarchy.
  If coordinates are absent or incomplete, or the geocoder cannot identify the
  village, the agent asks only for the missing state/village/district details.
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
