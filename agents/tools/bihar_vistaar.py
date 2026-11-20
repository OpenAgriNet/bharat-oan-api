"""BH -> BiharVistaar cross-network scheme search."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from langfuse import observe
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger

logger = get_logger(__name__)

DEFAULT_SCHEME_DOMAIN = "bihar-scheme:oan"
DEFAULT_SEARCH_TTL = "PT30S"


def _scheme_domain() -> str:
    return (os.getenv("BIHAR_SCHEME_DOMAIN") or DEFAULT_SCHEME_DOMAIN).strip() or DEFAULT_SCHEME_DOMAIN


def _bap_search_url() -> str:
    endpoint = (os.getenv("BAP_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("BAP_ENDPOINT is not configured")
    if endpoint.endswith("/search"):
        return endpoint
    return f"{endpoint}/search"


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_scheme_search_payload(query: str) -> Dict[str, Any]:
    return {
        "context": {
            "domain": _scheme_domain(),
            "action": "search",
            "version": "1.1.0",
            "bap_id": os.getenv("BAP_ID"),
            "bap_uri": os.getenv("BAP_URI"),
            "transaction_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "timestamp": _iso_timestamp(),
            "ttl": os.getenv("BIHAR_SEARCH_TTL", DEFAULT_SEARCH_TTL),
        },
        "message": {"intent": {"descriptor": {"name": (query or "").strip()}}},
    }


class BiharCrossNetworkSchemeResponse(BaseModel):
    responses: list = Field(default_factory=list)

    def _items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for block in self.responses:
            if not isinstance(block, dict):
                continue
            catalog = (block.get("message") or {}).get("catalog") or {}
            for provider in catalog.get("providers") or []:
                items.extend(provider.get("items") or [])
        return items

    def __str__(self) -> str:
        items = self._items()
        if not items:
            return "No scheme data found for this scheme."

        parts: List[str] = ["**Source:** Government Scheme Information", ""]
        for item in items:
            descriptor = item.get("descriptor") or {}
            parts.append(f"## {(descriptor.get('name') or item.get('id') or 'Scheme').strip()}")
            short_desc = (descriptor.get("short_desc") or "").strip()
            if short_desc:
                parts.append(short_desc)
            for tag in item.get("tags") or []:
                name = (tag.get("name") or "").strip()
                value = str(tag.get("value") or "").strip()
                if name and value and value.lower() != "null" and tag.get("display", True):
                    parts.append(f"- {name}: {value}")
            parts.append("")
        return "\n".join(parts).strip()


@observe(name="tool:call_bihar_vistaar_network", as_type="tool")
async def call_bihar_vistaar_network(
    ctx: RunContext[FarmerContext],
    query: str,
) -> str:
    """Fetch Bihar Krishi scheme info via network search.

    Use for Bihar state agriculture scheme queries (seed subsidy, farm machinery,
    irrigation, etc.) available over the Bihar cross-network catalog. Do not use
    for national schemes handled by search_schemes.

    Args:
        query: Short free-text scheme query, e.g. "seed subsidy".
    """
    query = (query or "").strip()
    if not query:
        raise ModelRetry("query is required for Bihar scheme search. Example: query='seed subsidy'.")

    payload = build_scheme_search_payload(query)
    payload["context"]["tags"] = {
        "session_id": ctx.deps.session_id or "",
        "question_id": ctx.deps.question_id or "",
    }

    lf_update_current_observation(
        input={"query": query},
        metadata={
            "tool": "call_bihar_vistaar_network",
            "cross_network": "bihar_vistaar",
            "query": query,
            "domain": payload["context"]["domain"],
            "transaction_id": payload["context"]["transaction_id"],
        },
    )

    try:
        url = _bap_search_url()
        logger.info(
            "Beckn [call_bihar_vistaar_network/search] URL: %s domain=%s",
            url,
            payload["context"]["domain"],
        )
        logger.info(
            "Beckn [call_bihar_vistaar_network/search] payload: %s",
            json.dumps(payload, ensure_ascii=False),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)

        logger.info("Beckn [call_bihar_vistaar_network/search] status: %s", response.status_code)

        if response.status_code != 200:
            logger.error(
                "BiharVistaar network search returned %s: %s",
                response.status_code,
                (response.text or "")[:500],
            )
            lf_update_current_observation(
                metadata={
                    "tool": "call_bihar_vistaar_network",
                    "http_status": int(response.status_code),
                }
            )
            return "Scheme information is temporarily unavailable. Please try again later."

        try:
            data = response.json()
        except json.JSONDecodeError:
            return (response.text or "").strip() or "BiharVistaar network returned a non-JSON response."

        responses = data.get("responses", data if isinstance(data, list) else [])
        if not isinstance(responses, list):
            responses = []

        result = str(BiharCrossNetworkSchemeResponse.model_validate({"responses": responses}))
        lf_update_current_observation(
            metadata={
                "tool": "call_bihar_vistaar_network",
                "has_scheme_data": "No scheme data found" not in result,
            }
        )
        return result

    except ValueError as e:
        logger.error("BiharVistaar cross-network config error: %s", e)
        return "BiharVistaar cross-network is not configured. Set BAP_ENDPOINT, BAP_ID, and BAP_URI."
    except httpx.TimeoutException:
        logger.error("BiharVistaar network request timed out")
        return "BiharVistaar request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("BiharVistaar network request failed: %s", e)
        return f"BiharVistaar request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected BiharVistaar network error: %s", e)
        raise ModelRetry(f"Unexpected error calling BiharVistaar network. {e!s}") from e