# bharat-oan-api

## Python runtime

Use **Python 3.15** for the API, IMD agromet loader, and prompt-contract CI.
The Docker dependency lock targets Linux amd64 and uses a CPython 3.15
CPU-only Torch wheel. `psycopg2-binary` is pinned to 2.9.13 for Python 3.15 wheels.

Python 3.15 wheels require pandas 3.1.0, pydantic 2.14.1 with pydantic-core
2.50.1, rapidfuzz 3.14.6, and zope-interface 8.7. The lock includes their
published Linux amd64 wheel hashes.

`marisa-trie==1.4.1` (used by language-data) and `PyYAML==6.0.3` do not publish
Python 3.15 wheels. Docker permits source builds **only for these two packages**,
using their existing locked source hashes, C++/libyaml system headers, and the
hash-locked tools in `requirements-build.lock`. Build isolation is disabled so
pip cannot silently install unpinned build tools. All other packages remain
binary-only, and `--require-hashes` remains enabled for both install steps.
Docker runs `pip check` and a native-import/language-data smoke check before
downloading the embedding model.

Published artifact metadata was checked without installing packages locally.
The Python 3.15 source builds and image smoke check still must pass in CI before
deployment. To regenerate the application lock where downloads are allowed
(do not pass `--only-binary=:all:`, since these two source builds are required):

```sh
uv pip compile requirements-docker.in --generate-hashes --python-version 3.15 --python-platform x86_64-manylinux_2_28 --default-index https://pypi.org/simple -o requirements.lock
```

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

Existing grievance services remain the default: PM-Kisan and PMFBY use their own
registration/status flows, and AIF uses its own status flow. Once the scheme or
service identifies the route, do not ask the farmer to choose between services.
An explicit request for KHC, Kisan Help Center/Centre
or Kisan Helpline Center/Centre overrides the default for that grievance.
The per-turn KHC reminder also recognizes the reported spelling "Kisan heliline
center". A KHC request that already names PM-Kisan proceeds to the first missing
KHC detail (state when no location is supplied), never a PM-Kisan registration
number. Replies contain one question with the KHC source last, not a second
follow-up question after the source.
Schemes without an existing flow use KHC directly. New AIF complaints also use KHC
because AIF's own integration supports tracking only. For registration, if the
scheme is missing, ask for it first, then route; that question alone does not select KHC.
Unrelated questions do not trigger KHC suggestions.

- **Status routing before identifiers:** "I want to know my grievance status"
  with no known scheme/service prompts "Could you please tell me which scheme
  or service your grievance is with?", without any service source label.
  PM-Kisan, PMFBY and AIF selections start their own existing status flows;
  an explicit KHC selection asks only for a missing grievance number.
  Reuse the route and identifiers for the same grievance. An unrelated earlier
  KHC request, a bare number, a loaded capability or the bot's own KHC source
  label must not select KHC for a new status request. Honor explicit service switches.
  This gate is supplied on every turn in every language, including before
  capability loading. Routing remains LLM-driven, not a deterministic state machine.
- **Registration order (all KHC schemes):** scheme, state/district/village,
  concern, matching, issue summary with the mapped sub-category, confirmation,
  mobile, OTP verification, new-farmer profile if required, submission.
  Reuse supplied details and ask only for missing information, one question at a time.
  `initiate_khc_otp` sends an OTP only after summary/sub-category confirmation;
  `verify_khc_otp` verifies it and returns `isRegistered`.
  While awaiting OTP, a six-digit reply goes directly to verification using the
  same mobile; leading zeros are preserved and regional digits are normalized.
  The agent must not reject it or request it again without a verification result.
  Actual invalid/expired OTP responses and service failures are distinguished;
  `find_khc_location` and `get_khc_grievance_options` resolve the required IDs
  before confirmation; `lookup_khc_reference` supplies fallback and change-option lists.
  Only after successful OTP verification, `isRegistered=false` triggers collection
  of any missing name, then gender (Male/Female/Other); do not infer gender.
  Already supplied profile details are reused. `isRegistered=true` skips both
  questions. Then `register_khc_grievance` submits the confirmed grievance;
  new-farmer profile collection does not restart complaint collection or confirmation.
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
  for it rather than guessing. `find_khc_location` resolves the sub-district from
  the village when possible. Ask for the sub-district only for an ambiguous
  village or as a fallback when the village cannot be found. Farmers select by
  name from API-backed choices (with further pages when available), not by number.
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
- **Problem matching:** capture the scheme first, then location and the farmer's
  own concern. Use only the scheme the farmer named and match the concern to
  that scheme's categories/sub-categories. Never infer a scheme from the concern
  or ask for internal IDs. The `name`
  filter is a literal substring, not semantic search; unfiltered options and
  further pages are checked when a keyword has no match. Already supplied details
  are reused instead of requesting a full registration questionnaire after OTP.
  AIF, A.I.F. and Agriculture Infrastructure Fund are treated as equivalent
  names when matching returned schemes; an already named AIF is not reconfirmed.
  The existing chat LLM matches categories/sub-categories by meaning against the
  full KHC catalogue returned privately by the tool, including names, IDs and
  parent-child relationships. No complaint synonym table or extra model service
  is needed. A missing provider keyword suggestion does not trigger a farmer-facing
  list: the LLM first compares meaning, paraphrases and negation. Only unresolved
  ambiguity triggers a focused clarification. Empty filters trigger unfiltered lookup
  and pagination; API failures are not reported as unsupported schemes.
  Missing mappings block readiness claims and final confirmation: there is no
  category-only submission or invented sub-category fallback.
- **Complaint retention:** a farmer's answer to the problem question is reused
  after category selection or later location questions, even if the model omits
  it from the next tool call. A direct ASCII answer to the problem question is
  sent intact (within the 500-character lookup limit), rather than allowing a
  model summary to drop matching words such as "bank" or "sanction".
  Non-ASCII complaints can still use an English translation, and later
  clarifications are retained. Selecting a category does not restart complaint
  collection. Match the complaint first: a clear category/sub-category match goes
  straight to the summary with no selection list. If only the category matches,
  offer only its sub-categories; show the category list only when no category
  clearly matches.
- **Summary and changes:** show the issue, scheme, resolved location, category
  and mapped sub-category name, with no internal IDs. Ask whether to confirm or
  change the sub-category. On an explicit change request, fetch all categories
  under the verified scheme and their sub-category lists, including remaining
  pages, and show names grouped by category. Update both parent category and
  sub-category IDs from the farmer's selection and obtain fresh confirmation.
  These are KHC-backed reference lists (provider database cache with API fallback),
  not hardcoded options.
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
