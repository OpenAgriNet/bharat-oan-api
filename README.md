# bharat-oan-api

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

