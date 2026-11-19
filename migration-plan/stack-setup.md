# Setting up the stack

Do this once. Everything here is shared by every capability migration — only
the provider you register and the catalog you publish change per capability.

When it is up, go to the capability you are working on:
[mandi/quick-try.md](mandi/quick-try.md).

## Before you start

- Docker with Compose v2.20 or later
- `python3` with `cryptography` (`pip install cryptography`) — the quick-start
  key generation needs it
- `openssl`, `curl`, `git`
- Credentials for the provider you are registering, and for a model endpoint.
  Neither is in either repo.

On Apple Silicon the app image runs under Rosetta. `requirements.lock` pins an
x86_64-only torch wheel under `--require-hashes`, so there is no arm64 build to
fall back to. It works, it is just slow to start.

---

## 1. The network

```sh
git clone -b mandi-migration-pilot git@github.com:OpenAgriNet/helmcharts.git
cd helmcharts/quick-start
cp .env.example .env

make up-core                               # registry and discovery
make keys                                  # this network's keypair
make register-network
make register-provider PROVIDER=<provider>  # asks for that provider's credentials
make configure-adapter                      # say no to crawling other networks
```

`make configure-adapter` asks about deployment mode and cross-network crawling.
Answer **no** to crawling: the pilot talks to local providers, and a crawler
pulls in catalogs that have nothing to do with it.

Providers and what each needs:

| provider | capability | needs |
|---|---|---|
| `pocra` | Agriculture Facility | nothing |
| `mausamgram` | Weather Observation | `MAUSAMGRAM_AUTH` |
| `agmarknet` | Mandi Price | base URL, token URL, access name, password |
| `vistaar` | Knowledge Advisory | base URL, token URL, client id, secret |
| `pmkisan` | Grievance and Scheme Application | two hosts, an AES key and nonce, two service tokens |

Check it:

```sh
make ps
curl -s localhost:9201/health
```

**The branch matters.** `docker-compose.override.yml` pins the adapter and
discovery to `0.2.0`. On the `0.0.1` images named in `dev.env` the adapter dies
at boot with `unrecognized step: checkCrossNetworkRequester` — a config step
that build does not have.

Every `*_MAPPING_URL` must point at a tag that exists in the published
helmcharts repo. One that 404s breaks every select for that capability, and
nothing in the error names the mapping.

---

## 2. Publish the capability's catalog

`discover` returns an empty list until something is published, and nothing in
the response says so — this is worth ruling out first whenever a tool reports
"no data". Use the Postman collection in `helmcharts/api-collection/`, or post the
capability's `publish` payload to the provider adapter directly.

Confirm a resource comes back before going near the AI layer:

```sh
curl -s localhost:9201/consumer/discover \
  -H 'Content-Type: application/json' \
  -d @your-discover-payload.json | head -40
```

---

## 3. This API

```sh
git clone -b mandi-migration-pilot https://github.com/OpenAgriNet/bharat-oan-api.git
cd bharat-oan-api
```

### Environment

Copy your `.env` into the repo root. It is gitignored and not in this repo; get
it from whoever runs the deployment.

### Model config

`config/models.yaml` points every alias at endpoints you probably do not have,
and the app validates **all aliases reachable from all use cases** at startup —
one missing `base_url` in a use case you will never call still stops the app.

Put your endpoint in `config/models.local.yaml`, which is gitignored and merged
over the tracked file key by key:

```yaml
models:
  openai_direct:
    kind: openai
    model_name: ${OPENAI_DIRECT_MODEL_NAME}
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_DIRECT_API_KEY}

use_cases:
  agrinet:        {routing_enabled: false, default_alias: openai_direct, aliases: [openai_direct], proportions: null}
  moderation:     {default_alias: openai_direct, aliases: [openai_direct]}
  suggestions:    {aliases: [openai_direct]}
  ffs_product_resolution: {default_alias: openai_direct, aliases: [openai_direct]}
  ffs_crop_resolution:    {default_alias: openai_direct, aliases: [openai_direct]}
```

All five are needed. `moderation` runs on every chat turn and has no off
switch; the two `ffs_*` ones are never reached by a price or weather question
but are still built at startup.

Routing is off deliberately. A migration measures whether a tool still gives
the same answer — a baseline recorded across two models is not a baseline.

### JWT keys

Auth is mandatory and the public key is read at **import time**, so the app
will not start without one. There is no env-based bypass. Both filenames are
already gitignored:

```sh
openssl genrsa -out private_key.pem 2048
openssl rsa -in private_key.pem -pubout -out jwt_public_key.pem
chmod 600 private_key.pem
```

These are local test credentials. They stay on your machine and are only good
against your own container.

### Start it

```sh
docker compose -f docker-compose.yml -f docker-compose.pilot.yml up -d
```

`docker-compose.pilot.yml` is what makes this a migration run rather than an
ordinary one. It sets the Beckn version flag, points the tools at the adapter
by container name, brings up the Redis the app needs, and starts the geocoder
below. It attaches to the network quick-start already created rather than
making its own — so start the network first.

### The geocoder

`photon_proxy` is an nginx shim in front of Photon. It exists because
`agents/tools/maps.py` builds a plain `http://host:port` URL and cannot dial an
https geocoder directly.

As shipped it forwards to Komoot's public Photon: real OpenStreetMap data, but
**the place names farmers type leave your machine** and the service carries no
availability promise. Local testing only. A deployment should run its own
Photon with an India index and point `PHOTON_HOST` at it.

Geocoding matters more than it looks — it is what turns "Rahuri" into the
coordinates `discover` filters on, and a place that geocodes to the wrong
district reads as "no data" rather than as an error.

---

## Four failures that cost the most time

All four are environment, not capability-specific, and none is obvious from
the error text:

| Symptom | Cause |
|---|---|
| `ValueError: Alias 'x': base_url is required` | The registry validates every reachable alias at boot, including use cases you never call |
| `FileNotFoundError: jwt_public_key.pem` in a crash loop | The public key is read at import time; no key, no boot |
| Adapter exits with `unrecognized step: checkCrossNetworkRequester` | Running `0.0.1` images; needs `0.2.0` |
| A tool says "no data" for everything | Catalog never published; `discover` returns empty and says nothing |

---

## Is it actually running?

```sh
docker compose ps                 # sva_app, sva_redis, photon_proxy
curl -s localhost:9201/health     # the adapter
```

`sva_app` has no `curl` inside it, so probe it from the host, not with
`docker exec`.

The app is behind `/api` — `/chat/` is a 404, `/api/chat/` is the endpoint.
