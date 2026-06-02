# Agent tools (MCP)

Tool implementations and JSON schemas are **not** maintained in this repository.

They run on the [MCP-OAN](https://github.com/OpenAgriNet/MCP-OAN) Python server (`MCP-OAN/python/server.py`). The Vistaar agent loads schemas from [`agents/data/vistaar_tools_manifest.json`](../data/vistaar_tools_manifest.json) (synced from MCP-OAN) and executes tools via `agents/mcp_toolsets.py` (`tools/call` only).

After adding a tool in MCP-OAN: run `python/scripts/export_vistaar_tools_manifest.py`, then `./scripts/sync-vistaar-tools-manifest.sh` in this repo.

See [`docs/tools-index.yaml`](../../docs/tools-index.yaml) for a minimal name-only catalog. Routing guidance remains in `assets/prompts/agrinet_*.md`.