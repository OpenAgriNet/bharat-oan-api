"""
Cross-network integration with MahaVistaar (mh-dev pattern).

Additive only — does not replace existing Bharat tools (weather, mandi,
legacy get_scheme_info, status, grievances, advisory).

Current scope: scheme *information* for the two NDKSP schemes enabled from
MahaVistaar:
  - drip-irrigation  → ndksp-drip-irrigation
  - inland-fishery   → ndksp-inland-fishery

Beckn contract (aligned with mh-oan-api scheme_info + cross_network):
  - Common context: domain / action / version / bap_* / ids / location / ttl
  - Message is use-case specific (schemes-agri item descriptor)
  - bpp_id / bpp_uri are NOT sent on search (only needed for init elsewhere)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

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

MAHA_VISTAAR_DOMAIN = "advisory:mh-vistaar"
DEFAULT_TIMEOUT = DEFAULT_HTTP_TIMEOUT

# Tool-facing code → full scheme name + MH network scheme_code
MAHA_VISTAAR_SCHEMES: Dict[str, Dict[str, str]] = {
    "drip-irrigation": {
        "scheme_name": "Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation",
        "network_code": "ndksp-drip-irrigation",
    },
    "inland-fishery": {
        "scheme_name": "Nanaji Deshmukh Krishi Sanjivani Prakalp Inland Fishery",
        "network_code": "ndksp-inland-fishery",
    },
}

MahaSchemeCode = Literal["drip-irrigation", "inland-fishery"]


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is not configured")
    return value


def _bap_action_url(action: str) -> str:
    """Resolve BAP URL for search/init/status — same idea as mh-dev cross_network."""
    base = (os.getenv("MAHA_VISTAAR_BAP_ENDPOINT") or os.getenv("BAP_BASE_URL") or "").strip().rstrip("/")
    if not base:
        endpoint = (os.getenv("BAP_ENDPOINT") or "").strip().rstrip("/")
        if endpoint.endswith("/search"):
            base = endpoint[: -len("/search")]
        else:
            base = endpoint
    if not base:
        raise ValueError(
            "MAHA_VISTAAR_BAP_ENDPOINT or BAP_ENDPOINT is not configured for MahaVistaar cross-network"
        )
    return f"{base}/{action.lstrip('/')}"


def _beckn_context(
    *,
    action: str,
    transaction_id: Optional[str] = None,
    session_id: str = "",
    question_id: str = "",
    include_bpp: bool = False,
) -> Dict[str, Any]:
    """
    Common Beckn context for MahaVistaar N-N calls.

    bpp_id / bpp_uri only when include_bpp=True (init). Search leaves them out
    (matches mh-dev SMAM search + scheme_info payloads).
    """
    now = datetime.now(timezone.utc)
    context: Dict[str, Any] = {
        "domain": MAHA_VISTAAR_DOMAIN,
        "action": action,
        "version": "1.1.0",
        "bap_id": os.getenv("MAHA_VISTAAR_BAP_ID") or os.getenv("BAP_ID"),
        "bap_uri": os.getenv("MAHA_VISTAAR_BAP_URI") or os.getenv("BAP_URI"),
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "ttl": "PT10M",
        "location": {"country": {"name": "IND"}},
        "tags": {
            "session_id": session_id or "",
            "question_id": question_id or "",
        },
    }
    if include_bpp or action == "init":
        # Optional targeting of MH BPP when configured (init only by default).
        bpp_id = (os.getenv("MAHA_VISTAAR_BPP_ID") or "").strip()
        bpp_uri = (os.getenv("MAHA_VISTAAR_BPP_URI") or "").strip()
        if bpp_id:
            context["bpp_id"] = bpp_id
        if bpp_uri:
            context["bpp_uri"] = bpp_uri
    return context


def _scheme_info_message(network_code: str) -> Dict[str, Any]:
    """Message section for MH scheme-info search (use-case specific)."""
    return {
        "intent": {
            "category": {"descriptor": {"code": "schemes-agri"}},
            "item": {"descriptor": {"code": network_code}},
        }
    }


def _scheme_info_payload(
    network_code: str,
    *,
    session_id: str = "",
    question_id: str = "",
) -> Dict[str, Any]:
    return {
        "context": _beckn_context(
            action="search",
            session_id=session_id,
            question_id=question_id,
            include_bpp=False,
        ),
        "message": _scheme_info_message(network_code),
    }


class CrossNetworkSchemeResponse(BaseModel):
    """Minimal formatter for Beckn aggregator / scheme catalog responses."""

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
            return "No scheme data found from MahaVistaar for this scheme."

        parts: List[str] = []
        parts.append("**Source:** Government Scheme Information")
        parts.append("")

        for block in self.responses:
            if not isinstance(block, dict):
                continue
            message = block.get("message") or {}
            catalog = message.get("catalog") or {}
            for provider in catalog.get("providers") or []:
                for item in provider.get("items") or []:
                    for tag in item.get("tags") or []:
                        for entry in tag.get("list") or []:
                            name = (entry.get("descriptor") or {}).get("name") or (
                                entry.get("descriptor") or {}
                            ).get("code")
                            value = entry.get("value")
                            if name and value and str(value).strip().lower() not in ("", "null"):
                                parts.append(f"## {name}")
                                parts.append(str(value).strip())
                                parts.append("")

        if len(parts) <= 2:
            return json.dumps(self.responses, ensure_ascii=False, indent=2)
        return "\n".join(parts).strip()


@observe(name="tool:get_maha_vistaar_scheme_info", as_type="tool")
async def get_maha_vistaar_scheme_info(
    ctx: RunContext[FarmerContext],
    scheme_code: MahaSchemeCode,
) -> str:
    """Fetch scheme information for MahaVistaar (Maharashtra) schemes via cross-network N-N.

    Use this for the two enabled NDKSP schemes only. All other Bharat schemes
    continue to use `get_scheme_info` or `search_schemes`.

    Args:
        scheme_code: One of:
            - "drip-irrigation": Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
            - "inland-fishery": Nanaji Deshmukh Krishi Sanjivani Prakalp Inland Fishery

    Returns:
        Formatted scheme information (eligibility, benefits, application, etc.).
    """
    scheme_code = (scheme_code or "").strip()
    if scheme_code not in MAHA_VISTAAR_SCHEMES:
        raise ModelRetry(
            'scheme_code must be "drip-irrigation" or "inland-fishery". '
            "For other schemes use get_scheme_info or search_schemes."
        )

    meta = MAHA_VISTAAR_SCHEMES[scheme_code]
    network_code = meta["network_code"]
    scheme_name = meta["scheme_name"]

    payload = _scheme_info_payload(
        network_code,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
    )
    transaction_id = payload["context"]["transaction_id"]

    lf_update_current_observation(
        input={
            "scheme_code": scheme_code,
            "network_code": network_code,
            "Scheme_name": f"scheme_name:{scheme_name}",
        },
        metadata={
            "tool": "maha_vistaar.scheme_info",
            "cross_network": "maha_vistaar",
            "scheme_code": scheme_code,
            "network_code": network_code,
            "transaction_id": transaction_id,
        },
    )

    try:
        endpoint = _bap_action_url("search")
        logger.info("Beckn [maha-vistaar/scheme-info] URL: %s", endpoint)
        logger.info(
            "Beckn [maha-vistaar/scheme-info] payload: %s",
            json.dumps(payload, ensure_ascii=False),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=DEFAULT_TIMEOUT)

        if response.status_code != 200:
            logger.error(
                "MahaVistaar scheme info returned %s: %s",
                response.status_code,
                (response.text or "")[:500],
            )
            lf_update_current_observation(
                metadata={"tool": "maha_vistaar.scheme_info", "http_status": int(response.status_code)}
            )
            return "MahaVistaar scheme information is temporarily unavailable. Please try again later."

        try:
            data = response.json()
        except json.JSONDecodeError:
            return (response.text or "").strip() or "MahaVistaar returned a non-JSON response."

        responses = data.get("responses", data if isinstance(data, list) else [])
        if not isinstance(responses, list):
            responses = []

        result = str(CrossNetworkSchemeResponse.model_validate({"responses": responses}))
        lf_update_current_observation(
            metadata={
                "tool": "maha_vistaar.scheme_info",
                "has_scheme_data": "No scheme data found" not in result,
            }
        )
        return result

    except ValueError as e:
        logger.error("MahaVistaar cross-network config error: %s", e)
        return (
            "MahaVistaar cross-network is not configured. "
            "Set MAHA_VISTAAR_BAP_ENDPOINT (or BAP_ENDPOINT)."
        )
    except httpx.TimeoutException:
        logger.error("MahaVistaar scheme info request timed out")
        return "MahaVistaar scheme request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("MahaVistaar scheme info request failed: %s", e)
        return f"MahaVistaar scheme request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected MahaVistaar scheme info error: %s", e)
        raise ModelRetry(f"Unexpected error calling MahaVistaar scheme info. {e!s}") from e
