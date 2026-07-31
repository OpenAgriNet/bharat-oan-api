"""BH → MahaVistaar cross-network scheme search (counterpart of MH bharat_vistaar)."""

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

DEFAULT_SCHEME_DOMAIN = "schemes:vistaar"


def _scheme_domain() -> str:
    return (os.getenv("SCHEME_DOMAIN") or DEFAULT_SCHEME_DOMAIN).strip() or DEFAULT_SCHEME_DOMAIN


def _bap_search_url() -> str:
    endpoint = (os.getenv("BAP_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("BAP_ENDPOINT is not configured")
    if endpoint.endswith("/search"):
        return endpoint
    return f"{endpoint}/search"


def build_scheme_search_payload(scheme_code: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "context": {
            "domain": _scheme_domain(),
            "action": "search",
            "version": "1.1.0",
            "bap_id": os.getenv("BAP_ID"),
            "bap_uri": os.getenv("BAP_URI"),
            "transaction_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "timestamp": str(int(now.timestamp())),
            "ttl": "PT10M",
            "location": {
                "country": {"code": "IND"},
                "city": {"code": "*"},
            },
        },
        "message": {
            "intent": {
                "category": {
                    "descriptor": {
                        "code": "schemes-agri",
                    }
                },
                "item": {
                    "descriptor": {
                        "name": scheme_code,
                    }
                },
            }
        },
    }


class CrossNetworkSchemeResponse(BaseModel):
    responses: list = Field(default_factory=list)

    def _has_data(self) -> bool:
        for block in self.responses:
            if not isinstance(block, dict):
                continue
            message = block.get("message") or {}
            catalog = message.get("catalog") or {}
            for provider in catalog.get("providers") or []:
                if provider.get("items"):
                    return True
        return False

    def __str__(self) -> str:
        if not self.responses or not self._has_data():
            return "No scheme data found for this scheme."

        parts: List[str] = ["**Source:** Government Scheme Information", ""]

        for block in self.responses:
            if not isinstance(block, dict):
                continue
            message = block.get("message") or {}
            catalog = message.get("catalog") or {}
            for provider in catalog.get("providers") or []:
                for item in provider.get("items") or []:
                    for tag in item.get("tags") or []:
                        for entry in tag.get("list") or []:
                            desc = entry.get("descriptor") or {}
                            name = desc.get("name") or desc.get("code")
                            value = entry.get("value")
                            if name and value and str(value).strip().lower() not in ("", "null"):
                                parts.append(f"## {name}")
                                parts.append(str(value).strip())
                                parts.append("")

        if len(parts) <= 2:
            return json.dumps(self.responses, ensure_ascii=False, indent=2)
        return "\n".join(parts).strip()


@observe(name="tool:call_maha_vistaar_network", as_type="tool")
async def call_maha_vistaar_network(
    ctx: RunContext[FarmerContext],
    scheme_code: str,
) -> str:
    """Fetch MahaVistaar scheme info via network search.

    Use for schemes listed under MahaVistaar / cross-network in the system prompt
    (e.g. ndksp-drip-irrigation, ndksp-farm-pond-lining). Pass scheme_code exactly
    as in the prompt. Do not use for get_scheme_info or search_schemes schemes.

    Args:
        scheme_code: Scheme code from the prompt (e.g. "ndksp-drip-irrigation").
    """
    scheme_code = (scheme_code or "").strip()
    if not scheme_code:
        raise ModelRetry(
            "scheme_code is required. Use a MahaVistaar scheme code from the system prompt "
            "(e.g. ndksp-drip-irrigation, ndksp-farm-pond-lining)."
        )

    payload = build_scheme_search_payload(scheme_code)
    payload["context"]["tags"] = {
        "session_id": ctx.deps.session_id or "",
        "question_id": ctx.deps.question_id or "",
    }

    lf_update_current_observation(
        input={"scheme_code": scheme_code},
        metadata={
            "tool": "call_maha_vistaar_network",
            "cross_network": "maha_vistaar",
            "scheme_code": scheme_code,
            "domain": payload["context"]["domain"],
            "transaction_id": payload["context"]["transaction_id"],
        },
    )

    try:
        url = _bap_search_url()
        logger.info(
            "Beckn [call_maha_vistaar_network/search] URL: %s domain=%s",
            url,
            payload["context"]["domain"],
        )
        logger.info(
            "Beckn [call_maha_vistaar_network/search] payload: %s",
            json.dumps(payload, ensure_ascii=False),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=DEFAULT_HTTP_TIMEOUT)

        logger.info("Beckn [call_maha_vistaar_network/search] status: %s", response.status_code)

        if response.status_code != 200:
            logger.error(
                "MahaVistaar network search returned %s: %s",
                response.status_code,
                (response.text or "")[:500],
            )
            lf_update_current_observation(
                metadata={
                    "tool": "call_maha_vistaar_network",
                    "http_status": int(response.status_code),
                }
            )
            return "Scheme information is temporarily unavailable. Please try again later."

        try:
            data = response.json()
        except json.JSONDecodeError:
            return (response.text or "").strip() or "MahaVistaar network returned a non-JSON response."

        responses = data.get("responses", data if isinstance(data, list) else [])
        if not isinstance(responses, list):
            responses = []

        result = str(CrossNetworkSchemeResponse.model_validate({"responses": responses}))
        lf_update_current_observation(
            metadata={
                "tool": "call_maha_vistaar_network",
                "has_scheme_data": "No scheme data found" not in result,
            }
        )
        return result

    except ValueError as e:
        logger.error("MahaVistaar cross-network config error: %s", e)
        return "MahaVistaar cross-network is not configured. Set BAP_ENDPOINT, BAP_ID, and BAP_URI."
    except httpx.TimeoutException:
        logger.error("MahaVistaar network request timed out")
        return "MahaVistaar request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("MahaVistaar network request failed: %s", e)
        return f"MahaVistaar request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected MahaVistaar network error: %s", e)
        raise ModelRetry(f"Unexpected error calling MahaVistaar network. {e!s}") from e
