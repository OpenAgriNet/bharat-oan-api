#!/usr/bin/env bash
# One-time setup: venvs, env files, pip installs for bharat-oan-api + MCP-OAN/python
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MCP_ROOT="$(cd "$ROOT/../MCP-OAN" && pwd)"
API_ENV="$ROOT/.env"
MCP_ENV="$MCP_ROOT/python/.env"

echo "==> bharat-oan-api venv"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

echo "==> MCP-OAN/python venv"
cd "$MCP_ROOT/python"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

echo "==> MCP python .env (from bharat-oan-api .env)"
if [[ -f "$API_ENV" ]]; then
  cp "$API_ENV" "$MCP_ENV"
  grep -q '^MCP_PORT=' "$MCP_ENV" 2>/dev/null || echo 'MCP_PORT=3001' >> "$MCP_ENV"
  grep -q '^MCP_HOST=' "$MCP_ENV" 2>/dev/null || echo 'MCP_HOST=127.0.0.1' >> "$MCP_ENV"
  grep -q '^MCP_TRANSPORT=' "$MCP_ENV" 2>/dev/null || echo 'MCP_TRANSPORT=streamable-http' >> "$MCP_ENV"
else
  echo "WARN: $API_ENV missing — copy python/.env.example manually"
fi

echo "==> bharat-oan-api MCP vars"
for line in \
  'MCP_SERVER_URL=http://127.0.0.1:3001/mcp' \
  'MCP_TIMEOUT_SECONDS=120' \
  'ENVIRONMENT=local' \
  'REDIS_HOST=127.0.0.1' \
  'REDIS_PORT=6379' \
  'USE_MEMORY_CACHE=true' \
  'LLM_AGRINET_MODEL_NAME=gpt-4.1' \
  'LLM_MODERATION_MODEL_NAME=gpt-4.1'; do
  key="${line%%=*}"
  if ! grep -q "^${key}=" "$API_ENV" 2>/dev/null; then
    echo "$line" >> "$API_ENV"
  fi
done

echo "Done. Run: ./scripts/start-local.sh"