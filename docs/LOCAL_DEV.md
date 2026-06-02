# Local dev: bharat-oan-api + MCP-OAN

## One-time setup

```bash
cd bharat-oan-api
./scripts/setup-local-mcp.sh
```

This creates venvs, copies `.env` into `../MCP-OAN/python/.env`, and appends:

- `MCP_SERVER_URL=http://127.0.0.1:3001/mcp`
- `USE_MEMORY_CACHE=true` (no Redis required)

## Start services

```bash
./scripts/start-local.sh
```

| Service | URL | Log |
|---------|-----|-----|
| MCP-OAN (Python) | http://127.0.0.1:3001/mcp | `.local-dev-logs/mcp.log` |
| bharat-oan-api | http://127.0.0.1:8000 | `.local-dev-logs/api.log` |

Stop: `./scripts/start-local.sh stop`

## Smoke test

```bash
./scripts/test-local-mcp.sh
```

Expect **29 tools** from MCP. Chat step needs a valid **Azure OpenAI** deployment in `.env` (`AZURE_OPENAI_DEPLOYMENT_NAME`, etc.).

## Manual chat

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/token \
  -H "Content-Type: application/json" \
  -d '{"mobile":"9999999999"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -N "http://127.0.0.1:8000/api/chat/?query=What%20is%20PM%20Kisan%3F&target_lang=en&source_lang=en&user_id=test" \
  -H "Authorization: Bearer $TOKEN"
```

## MCP-OAN only (separate terminal)

```bash
cd ../MCP-OAN/python
source .venv/bin/activate
export USE_MEMORY_CACHE=true
python server.py
```

## Optional: Redis instead of memory cache

Remove `USE_MEMORY_CACHE=true` from `.env` and run Redis (`docker run -p 6379:6379 redis:7-alpine` or local `redis-server`).