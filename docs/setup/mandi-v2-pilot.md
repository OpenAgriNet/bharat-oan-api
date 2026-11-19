# Running the mandi v1 -> v2 pilot

Brings up one OAN network and this API next to it, then asks a farmer question
and watches it come back over Beckn v2.

Only mandi moves to v2. Weather and the other fifteen tools still post to the
v1 BAP, so they answer only if you have that running too — see
[What does not work](#what-does-not-work).

## Before you start

- Docker with Compose v2.20 or later
- `python3` with `cryptography` (`pip install cryptography`) — the quick-start
  key generation needs it
- `openssl`, `curl`, `git`
- Credentials for Agmarknet (base URL, token URL, access name, password) and
  for your model endpoint. Neither is in either repo.

On Apple Silicon the app image runs under Rosetta. `requirements.lock` pins an
x86_64-only torch wheel under `--require-hashes`, so there is no arm64 build to
fall back to. It works, it is just slow to start.

## 1. The network

```sh
git clone -b mandi-migration-pilot git@github.com:OpenAgriNet/helmcharts.git
cd helmcharts/quick-start
cp .env.example .env

make up-core                               # registry and discovery
make keys                                  # this network's keypair
make register-network
make register-provider PROVIDER=agmarknet  # asks for the four credentials
make configure-adapter                     # say no to crawling other networks
```

`make configure-adapter` asks about deployment mode and cross-network crawling.
Answer **no** to crawling: the pilot talks to one local provider, and a crawler
would pull in catalogs that have nothing to do with it.

Check it:

```sh
make ps
curl -s localhost:9201/health
```

The branch matters. `docker-compose.override.yml` pins the adapter and
discovery to `0.2.0`; on the `0.0.1` images named in `dev.env` the adapter dies
at boot with `unrecognized step: checkCrossNetworkRequester`.

## 2. Publish the mandi catalog

The tool's `discover` call finds nothing until a market is published. Use the
Postman collection under `api-collection/`, or post the capability's `publish`
payload to the provider adapter directly.

Confirm a market comes back before going further — everything downstream is
silent about an empty catalog:

```sh
curl -s localhost:9201/consumer/discover \
  -H 'Content-Type: application/json' \
  -d @your-discover-payload.json | head -40
```

## 3. This API

```sh
git clone -b mandi-migration-pilot https://github.com/OpenAgriNet/bharat-oan-api.git
cd bharat-oan-api
```

**Environment.** Copy your `.env` into the repo root. It is gitignored and is
not in this repo; get it from whoever runs the deployment.

**Model config.** `config/models.yaml` points every alias at endpoints you
probably do not have, and the app validates *all* of them at startup — one
missing `base_url` and it will not boot. Put your endpoint in
`config/models.local.yaml`, which is gitignored and merged over the tracked
file key by key:

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

All five use cases are needed. `moderation` runs on every chat turn and has no
off switch; the two `ffs_*` ones are never reached by the mandi path but are
still built at startup.

**JWT keys.** Auth is mandatory and the public key is read at import time, so
the app will not start without one. Both filenames are already gitignored:

```sh
openssl genrsa -out private_key.pem 2048
openssl rsa -in private_key.pem -pubout -out jwt_public_key.pem
chmod 600 private_key.pem
```

These are local test credentials. They stay on your machine and are only good
against your own container.

**Start it.**

```sh
docker compose -f docker-compose.yml -f docker-compose.pilot.yml up -d
```

`docker-compose.pilot.yml` is what makes this the pilot rather than an ordinary
run. It sets `MANDI_BECKN_VERSION: v2`, points the tool at the adapter by
container name, brings up the Redis the app needs, and starts the geocoder
below. It attaches to the network quick-start already created rather than
making its own, so start the network first.

**The geocoder.** `photon_proxy` is an nginx shim in front of Photon. It exists
because `agents/tools/maps.py` builds a plain `http://host:port` URL and cannot
dial an https geocoder directly. As shipped it forwards to Komoot's public
Photon: real OpenStreetMap data, but the place names leave your machine and
there is no availability promise. **Local testing only.** A deployment should
run its own Photon with an India index and point `PHOTON_HOST` at it.

## 4. Ask it something

Two ways, and the difference matters.

**The tool on its own** — calls `get_mandi_prices` directly, no model involved.
Use this to check the Beckn v2 calls:

```sh
docker exec -e PYTHONPATH=/app sva_app python scripts/pilot_mandi_check.py
```

Seven fixed cases: a date range, a single day, a day with no arrivals, latest
available, a second market, a location with no market within 50 km, and the
over-30-day guard. Or ask your own:

```sh
docker exec -e PYTHONPATH=/app sva_app python scripts/pilot_mandi_check.py \
  --place Rahuri --commodity Onion --from 01-09-2026 --to 09-09-2026
```

**The whole chat path** — the model decides everything. Nothing here names a
tool:

```sh
./scripts/pilot_chat.sh --lang en "onion price in Rahuri from 1 to 9 September"
```

Expect the four September arrival dates. To prove it really went over v2:

```sh
docker logs sva_app 2>&1 | grep mandi_v2
# Mandi v2 discover -> http://network-adapter:9201/consumer/discover
# Mandi v2 select   -> http://network-adapter:9201/consumer/select
```

Flag ordering is fussy: `--lang en "question"`, not the other way round.
Anything after the question is ignored.

## What does not work

**Weather, advisory, PM-Kisan, PMFBY, SATHI seed and eleven others** still
build a v1 payload and post it to `BAP_ENDPOINT/search`. Without that BAP
running you get `Connection refused` and the farmer sees "I could not get
that". Nothing in the v2 work touched them.

This also means the mandi answer's closing offer — "would you like the weather
forecast?" — leads nowhere unless the v1 BAP is up.

**Known rough edges in the v2 path:**

- A date with no arrivals comes back from the adapter as HTTP 502
  `NET_DOWNSTREAM_UNAVAILABLE`, because Agmarknet answers it with a 400 and
  nothing maps that to an empty result. The tool swallows it and reports
  NO_DATA, which is what v1 did. It is lossy: a real provider outage now reads
  as NO_DATA too. The fix belongs in the MandiPrice select action mapping.
- `discover` returns an unranked list, so picking the market, ranking by
  distance, falling back to a nearby mandi, and turning a commodity name into a
  code all happen in the tool. In v1 the provider did all four. Worth fixing
  network-side before the other capabilities migrate, or every tool will
  reimplement it.
- No v1 baseline has been recorded, because the v1 BAP was not running. The v2
  answers are verified as correct; they are not yet verified as *identical to
  what v1 said*.

## Turning the pilot off

Unset `MANDI_BECKN_VERSION` — or run without `docker-compose.pilot.yml` — and
`agents/tools/__init__.py` imports the v1 module again. Nothing else changes:
both modules export the same function name, signature and docstring, so the
agent, the skill prompt and the rendered answer are identical either way.
