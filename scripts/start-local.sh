#!/usr/bin/env bash
# Start Redis (if needed), MCP-OAN python server, and bharat-oan-api for local testing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MCP_ROOT="$(cd "$ROOT/../MCP-OAN" && pwd)"
LOG_DIR="${ROOT}/.local-dev-logs"
mkdir -p "$LOG_DIR"

redis_ok() {
  redis-cli -h "${REDIS_HOST:-127.0.0.1}" -p "${REDIS_PORT:-6379}" ping 2>/dev/null | grep -q PONG
}

free_ports() {
  for port in 3001 8000; do
    pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
    if [[ -n "${pids}" ]]; then
      echo "Freeing port ${port} (pids: ${pids})"
      kill -9 ${pids} 2>/dev/null || true
    fi
  done
  sleep 1
}

start_redis() {
  if [[ "${USE_MEMORY_CACHE:-}" == "true" ]] || grep -q '^USE_MEMORY_CACHE=true' "$ROOT/.env" 2>/dev/null; then
    echo "Using in-memory cache (USE_MEMORY_CACHE=true) — skipping Redis"
    return
  fi
  if redis_ok; then
    echo "Redis already running"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    echo "Starting Redis via Docker..."
    docker rm -f bharat-oan-redis 2>/dev/null || true
    docker run -d --name bharat-oan-redis -p 6379:6379 redis:7-alpine
    sleep 2
  elif command -v redis-server >/dev/null 2>&1; then
    echo "Starting redis-server in background..."
    redis-server --daemonize yes --port 6379
    sleep 1
  else
    echo "WARN: Redis not available. Add USE_MEMORY_CACHE=true to .env and re-run, or start Redis/Docker."
    echo "Attempting to continue (chat history will not persist without cache)..."
    return
  fi
  redis_ok || { echo "Redis failed to start"; exit 1; }
}

stop_services() {
  echo "Stopping local dev processes..."
  [[ -f "$LOG_DIR/mcp.pid" ]] && kill "$(cat "$LOG_DIR/mcp.pid")" 2>/dev/null || true
  [[ -f "$LOG_DIR/api.pid" ]] && kill "$(cat "$LOG_DIR/api.pid")" 2>/dev/null || true
  rm -f "$LOG_DIR/mcp.pid" "$LOG_DIR/api.pid"
}

if [[ "${1:-}" == "stop" ]]; then
  stop_services
  exit 0
fi

if [[ "${1:-}" == "status" ]]; then
  echo "MCP log: tail -f $LOG_DIR/mcp.log"
  echo "API log: tail -f $LOG_DIR/api.log"
  curl -sf "http://127.0.0.1:3001/mcp" >/dev/null 2>&1 && echo "MCP :3001 reachable" || echo "MCP :3001 not reachable (may be OK for POST-only)"
  curl -sf "http://127.0.0.1:8000/api/health/live" && echo "" && echo "API :8000 live"
  exit 0
fi

free_ports
start_redis
stop_services

echo "Starting MCP-OAN python server on :3001..."
cd "$MCP_ROOT/python"
source .venv/bin/activate
nohup python server.py >"$LOG_DIR/mcp.log" 2>&1 &
echo $! >"$LOG_DIR/mcp.pid"
deactivate

echo "Waiting for MCP server..."
for i in $(seq 1 30); do
  if curl -sf -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:3001/mcp" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' 2>/dev/null | grep -qE '200|406'; then
    echo "MCP server up"
    break
  fi
  sleep 1
  if [[ $i -eq 30 ]]; then
    echo "MCP may still be starting — check: tail -f $LOG_DIR/mcp.log"
  fi
done

echo "Starting bharat-oan-api on :8000..."
cd "$ROOT"
source .venv/bin/activate
nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload >"$LOG_DIR/api.log" 2>&1 &
echo $! >"$LOG_DIR/api.pid"

sleep 3
curl -sf "http://127.0.0.1:8000/api/health/live" >/dev/null && echo "API health OK" || echo "WARN: API not ready — tail -f $LOG_DIR/api.log"

echo ""
echo "Services:"
echo "  MCP:  http://127.0.0.1:3001/mcp  (log: $LOG_DIR/mcp.log)"
echo "  API:  http://127.0.0.1:8000       (log: $LOG_DIR/api.log)"
echo ""
echo "Smoke test:"
echo "  ./scripts/test-local-mcp.sh"
echo ""
echo "Stop:"
echo "  ./scripts/start-local.sh stop"