"""
MCP toolset for the Vistaar agent — tools are defined and executed on the MCP-OAN server.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic_ai.mcp import CallToolFunc, MCPServerStreamableHTTP, ToolResult
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from app.config import settings

NPSS_SOURCE_NAME = "National Pest Surveillance System (NPSS)"
NPSS_SOURCE_OWNER = (
    "Department of Agriculture & Farmers Welfare, Ministry of Agriculture & Farmers Welfare, "
    "Government of India"
)
NPSS_SOURCE_URL = "https://npss.dac.gov.in/"


def _mcp_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if settings.mcp_api_key:
        headers["Authorization"] = f"Bearer {settings.mcp_api_key}"
        headers["x-api-key"] = settings.mcp_api_key
    return headers


def _build_http_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(settings.mcp_timeout_seconds, connect=10.0)
    return httpx.AsyncClient(headers=_mcp_headers(), timeout=timeout)


async def inject_farmer_context(
    ctx: RunContext[FarmerContext],
    call_tool: CallToolFunc,
    name: str,
    tool_args: dict[str, Any],
) -> ToolResult:
    """Attach session/location metadata for MCP tools; handle NPSS post-process flags."""
    meta = {
        "deps": {
            "query": ctx.deps.query,
            "lang_code": ctx.deps.lang_code,
            "session_id": ctx.deps.session_id,
            "moderation_str": ctx.deps.moderation_str,
            "latitude": ctx.deps.latitude,
            "longitude": ctx.deps.longitude,
        }
    }
    result = await call_tool(name, tool_args, meta)

    if name == "analyze_crop_image":
        text = _tool_result_text(result)
        if text and "**NPSS Analysis Result**" in text:
            ctx.deps.mark_npss_result(
                raw_result={"text": text},
                source_name=NPSS_SOURCE_NAME,
                source_owner=NPSS_SOURCE_OWNER,
                source_url=NPSS_SOURCE_URL,
            )

    return result


def _tool_result_text(result: ToolResult) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts)
    return str(result) if result is not None else ""


def create_vistaar_mcp_toolset() -> MCPServerStreamableHTTP:
    return MCPServerStreamableHTTP(
        settings.mcp_server_url,
        headers=_mcp_headers(),
        http_client=_build_http_client(),
        process_tool_call=inject_farmer_context,
        timeout=settings.mcp_timeout_seconds,
        read_timeout=settings.mcp_timeout_seconds,
    )


vistaar_mcp = create_vistaar_mcp_toolset()