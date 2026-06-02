#!/usr/bin/env bash
# Smoke test: MCP tool list + optional one chat turn
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate

echo "==> 1. MCP tools/list via Pydantic AI client"
python3 <<'PY'
import asyncio
import os
from dotenv import load_dotenv

from pathlib import Path
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from pydantic_ai.mcp import MCPServerStreamableHTTP

url = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")

async def main():
    server = MCPServerStreamableHTTP(url, timeout=30, read_timeout=30)
    async with server:
        tools = await server.list_tools()
        names = sorted(t.name for t in tools)
        print(f"Connected to {url}")
        print(f"Tool count: {len(names)}")
        for n in names[:5]:
            print(f"  - {n}")
        if len(names) > 5:
            print(f"  ... and {len(names) - 5} more")
        assert len(names) >= 29, f"expected >=29 tools, got {len(names)}"

asyncio.run(main())
PY

echo ""
echo "==> 2. API health"
curl -sf "http://127.0.0.1:8000/api/health/live" | python3 -m json.tool

echo ""
echo "==> 3. JWT + chat (scheme info — needs Azure OpenAI in .env)"
TOKEN=$(curl -sf -X POST "http://127.0.0.1:8000/api/token" \
  -H "Content-Type: application/json" \
  -d '{"mobile":"9999999999","name":"local-dev","role":"public"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "Got JWT (${#TOKEN} chars)"

# Short query; streams response
echo "Chat response (first 500 chars):"
curl -sf -N "http://127.0.0.1:8000/api/chat/?query=What%20is%20PM%20Kisan%20scheme%3F&target_lang=en&source_lang=en&user_id=local-test" \
  -H "Authorization: Bearer ${TOKEN}" 2>&1 | head -c 500
echo ""
echo ""
echo "Done. Full stream: curl -N 'http://127.0.0.1:8000/api/chat/?query=...' -H 'Authorization: Bearer \$TOKEN'"