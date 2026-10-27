# bharat-oan-api

## Release scope

This development snapshot temporarily removes KHC grievance services, IMD agromet
advisories and ingestion, and AgriStack integration so it can be promoted to
production. Their tools, workflow prompts, callback routes and feature-only
database dependency are absent, not merely hidden from the prompt. Existing
explicit tool allowlists must omit removed tool names or policy validation fails.

KCC status, mandi prices, ordinary weather forecasts, ICAR/document crop advice,
and the existing PM-Kisan/PMFBY grievance and AIF tracking flows remain available.
The capability loader, language prompts and general chat/streaming improvements
are retained. No provider-backend or live deployment configuration is changed.

Restore the three features only after the production merge is confirmed, by
reverting the dedicated removal commit on development and rerunning tests.
A later full development-to-production merge would also include that restoration.

Run the offline release checks:

```sh
python -m pytest -q tests/test_release_feature_scope.py tests/test_capabilities.py \
  tests/test_retained_tool_calls.py tests/test_prompt_language_contract.py \
  tests/test_advisory_prompts.py tests/test_pmkisan_prompt_validation.py \
  tests/test_stream_dedupe.py tests/test_guided_decoding.py
```

The existing `test_prompt_inventory_and_language_coverage` check has a separate
baseline failure: the English template inlines sections that its manifest lists
as includes. This predates the removal and is not changed by this release.

## Python runtime

Use **Python 3.15** for the API and prompt-contract CI.
The Docker dependency lock targets Linux amd64 and uses a CPython 3.15
CPU-only Torch wheel.

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
