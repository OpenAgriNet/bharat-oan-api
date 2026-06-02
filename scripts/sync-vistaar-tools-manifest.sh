#!/usr/bin/env bash
# Copy the canonical Vistaar tools manifest from MCP-OAN into this repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MCP_OAN_ROOT="${MCP_OAN_ROOT:-$(dirname "$ROOT")/MCP-OAN}"
SRC="$MCP_OAN_ROOT/shared/vistaar_tools_manifest.json"
DEST="$ROOT/agents/data/vistaar_tools_manifest.json"

if [[ ! -f "$SRC" ]]; then
  echo "Missing manifest at $SRC" >&2
  echo "Run in MCP-OAN: python/scripts/export_vistaar_tools_manifest.py" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
cp "$SRC" "$DEST"
echo "Synced $(wc -l < "$DEST" | tr -d ' ') lines -> $DEST"