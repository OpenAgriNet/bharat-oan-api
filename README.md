# bharat-oan-api
 
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

- Base callback: `/api/callback?from=agristack`
- Wildcard callback: `/api/callback/<any-sub-path>?from=agristack`
- Status check: `/api/callback/status?from=agristack&callbackSessionId=<id>`

Supported methods: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`

The callback response includes:
- `from` query param value
- all query params
- selected request headers
- parsed body (`json` / `form` / `raw`)

`callbackSessionId` behavior:
- When callback POST includes `callbackSessionId`, backend stores receipt status in cache.
- Frontend then checks status via `GET /api/callback/status` using the same `callbackSessionId`.

