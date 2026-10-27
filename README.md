# bharat-oan-api

## Python runtime

Docker and both prompt/KCC-mandi CI jobs use **Python 3.15**. The Docker
dependency lock targets Linux amd64 and selects the official **Torch 2.14.0+cpu**
CPython 3.15 wheel with its SHA256.

Python 3.15 wheel compatibility requires pandas 3.1.0, pydantic 2.14.1 with
pydantic-core 2.50.1, rapidfuzz 3.14.6, and zope-interface 8.7. The older
`bh-main` baseline also requires httptools 0.9.0, NumPy 2.5.4, and SciPy 1.18.1.
Other application pins are retained.

Only `marisa-trie==1.4.1` and `PyYAML==6.0.3` build from source because they
have no CPython 3.15 wheels. Docker installs C++/libyaml headers and the
hash-locked tools in `requirements-build.lock` first, then disables build
isolation. All other packages remain binary-only and both installation steps
enforce hashes. The image runs `pip check`, native imports, a language-data
check, and a CPU-only Torch check before downloading the embedding model.

Artifact metadata and configuration can be checked without installing packages
locally; source compilation and the Python 3.15 image still require CI validation.
To regenerate the application lock where downloads are allowed:

```sh
uv pip compile requirements-docker.in --generate-hashes --python-version 3.15 --python-platform x86_64-manylinux_2_28 --default-index https://pypi.org/simple -o requirements.lock
```

## KCC and mandi support

- KCC application status: send an OTP to the application mobile number, check
  status with that OTP, then select an application if several are returned.
  Selecting another listed application does not send or reuse an OTP; changing
  the mobile number starts a new check. KCC scheme information continues to use
  `get_scheme_info("kcc")`; this change does not add KCC grievance filing.
- Mandi prices: explicit dates/date ranges, latest available prices, paginated
  results, and nearby-market suggestions. Prices from a substitute market are
  withheld until the farmer chooses it. Provider date-window limits remain in
  effect, and unavailable services are not reported as empty market results.
- Both flows use the existing `BAP_ENDPOINT`, `BAP_ID`, `BAP_URI`, `BPP_ID`, and
  `BPP_URI` configuration. The provider must support KCC (`kcc-agri` /
  `kcc-status`) and mandi search-context responses; this repository does not
  deploy that provider.
- Commodity names and aliases are aligned with the Agmarknet master.
  `scripts/sync_commodity_codes.py` supports a saved `--master` response and
  `--dry-run`; live synchronization requires `AGMARKNET_BASE_URL`,
  `AGMARKNET_ACCESS_NAME`, and `AGMARKNET_PASSWORD`. These are maintenance-only
  settings, not new API runtime requirements.

### Offline feature checks

The KCC/mandi tests isolate HTTP, tracing, configuration and model-client startup;
they require no API keys or running provider. In a separate virtual environment:

```bash
python -m pip install pytest pytest-cov pydantic-ai-slim==1.32.0 pydantic==2.14.1 httpx==0.28.1 \
  python-dateutil==2.9.0.post0 pytz python-dotenv Jinja2==3.1.6 rapidfuzz==3.14.6
python -m pytest -q tests/test_kcc.py tests/test_mandi.py \
  tests/test_mandi_pagination.py tests/test_commodity.py \
  tests/test_prompt_language_contract.py --cov=agents/tools --cov=scripts --cov-report=
python -m coverage report --include='agents/tools/kcc.py,agents/tools/mandi.py,agents/tools/commodity.py,scripts/sync_commodity_codes.py' --fail-under=80
```

The feature checks run in GitHub Actions alongside the localized prompt checks.
Production image publishing remains on the existing `bh-main` workflow.


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
