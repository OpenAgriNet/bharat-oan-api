"""
Scheme information search -- one tool, search_schemes: national scheme guidelines on the Knowledge Provider,
the MahaVistaar and AmulVistaar cross-network catalogs, other agricultural documents for schemes outside the
catalog, and eNAM training videos.
"""
import asyncio
import os
import re
from typing import Any, Dict, List, Literal, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.amul_vistaar import call_amul_vistaar_network
from agents.tools.bihar_vistaar import call_bihar_vistaar_network
from agents.tools.maha_vistaar import call_maha_vistaar_network
from helpers import knowledge_provider
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.scheme_qdrant_search import (
    NO_RESULTS_MESSAGE,
    _format_page,
    classify_chunk_section,
    format_search_results,
    format_status_check_offer,
)
from helpers.utils import get_logger

logger = get_logger(__name__)

# Langfuse category label for KP scheme searches.
SCHEME_SEARCH_CATEGORY = "knowledge-provider-scheme"


def _scheme_result_from_hit(hit: Dict[str, Any], score: float) -> Optional[Dict[str, Any]]:
    """Map one KP scheme hit to the format expected by format_search_results."""
    if not isinstance(hit, dict):
        return None
    text = str(hit.get("text") or "").strip()
    if not text:
        return None
    return {
        "score": score,
        "scheme_code": str(hit.get("scheme_code") or hit.get("code") or "").strip(),
        "scheme_name": str(hit.get("scheme_name") or hit.get("name_en") or hit.get("name") or "").strip(),
        "text": text,
        "doc_id": str(hit.get("doc_id") or "").strip(),
        "chunk_id": str(hit.get("chunk_id") or hit.get("_id") or "").strip(),
        # KP's section_type (eligibility, exclusion, application, support, other) is the old network "section".
        "section": str(hit.get("section_type") or "").strip().lower() or "other",
        # KP's origin is the intake channel, not a citation; format_search_results falls back to its label.
        "source": "",
        "doc_title": str(hit.get("title_en") or hit.get("name_en") or hit.get("filename") or "").strip(),
        "page": _format_page(hit.get("page_start"), hit.get("page_end")),
    }


def _parse_scheme_search_response(data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Parse the KP /search response (scheme profile) into (chunk results, search context)."""
    if not isinstance(data, dict):
        return [], {}
    results = []
    for hit in data.get("hits") or []:
        parsed = _scheme_result_from_hit(hit, knowledge_provider.hit_score(hit, data) if isinstance(hit, dict) else 0.0)
        if parsed:
            results.append(parsed)
    return results, knowledge_provider.context_strings(data.get("scheme_search"))


def _build_scheme_search_payload(query: str, top_k: int = 10) -> Dict[str, Any]:
    """KP /search body for scheme guideline search (search_profile scheme). Plain semantic search: no scheme filter."""
    body: Dict[str, Any] = {"query": query, "search_profile": "scheme"}
    if top_k and top_k > 0:
        body["top_k"] = min(top_k, 50)
    return body


_ENAM = re.compile(r"\be[\s-]?nam\b", re.IGNORECASE)


@observe(name="tool:search_schemes", as_type="tool")
async def search_schemes(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
    network: Literal["national", "mahavistaar", "amul", "bihar", "other_documents"] = "national",
    scheme_code: Optional[str] = None,
    union: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> str:
    """
    Search government scheme information: what a scheme is, who is eligible, benefits, how to apply.

    Use for every scheme-information question. For a direct status/grievance request (e.g. "check my
    PM-KISAN status"), call the status tools instead -- do not call this first.

    - network="national" (default): scheme guideline documents for every catalog scheme (PM-KISAN, PMFBY,
      KCC, AIF, SHC, SMAM, N.B.M., eNAM ...), each result labelled with its scheme, document and page.
      eNAM questions also get related training videos.
    - network="mahavistaar": the Maharashtra cross-network schemes; pass `scheme_code`
      ("ndksp-drip-irrigation", "ndksp-farm-pond-lining" or "aif" -- drip irrigation under AIF only).
    - network="amul": Amul union schemes; optional `union` ("banas", "kutch", "sumul", "surendranagar")
      or `provider_id` (e.g. "banas-union").
    - network="bihar": Bihar state agriculture schemes (seed subsidy, farm machinery, irrigation ...)
    - network="other_documents": only when the national search found nothing relevant for a scheme (for
      example a state scheme): searches the other agricultural documents.

    Args:
        query: The scheme question in English, 2-5 words (eligibility, benefits, application process) (required)
        top_k: Maximum number of chunks to return (default: 10)
        network: Which source to search: "national", "mahavistaar", "amul", "bihar" or "other_documents"
        scheme_code: MahaVistaar scheme code, only with network="mahavistaar"
        union: Amul union filter, only with network="amul"
        provider_id: Amul canonical provider ID, only with network="amul"

    Returns:
        Formatted scheme information with its source (and related videos for eNAM).
    """
    if network == "mahavistaar":
        return await call_maha_vistaar_network(ctx, scheme_code or query)
    if network == "amul":
        return await call_amul_vistaar_network(ctx, query, union=union, provider_id=provider_id)
    if network == "bihar":
        return await call_bihar_vistaar_network(ctx, query)
    if network == "other_documents":
        return await _search_other_documents(query)
    result = await _search_national_schemes(ctx, query, top_k)
    if _ENAM.search(query):
        result += await _enam_videos(query)
    return result


async def _enam_videos(query: str, top_k: int = 3) -> str:
    """Related eNAM training videos from Qdrant; empty when none or the video search fails."""
    try:
        from helpers.video_qdrant_search import format_video_search_results, search_videos

        if not os.getenv("QDRANT_URL"):
            raise ValueError("QDRANT_URL is required")
        collection = os.getenv("QDRANT_VIDEO_COLLECTION_NAME", "video_data_collection")
        results = await asyncio.to_thread(search_videos, query, collection, top_k)
    except Exception as e:  # videos are extra: never fail the scheme answer for them
        logger.error("eNAM video search failed: %s for query: %s", e, query)
        return ""
    return "\n\n" + format_video_search_results(results, query) if results else ""


async def _search_other_documents(query: str) -> str:
    """A scheme the national search has nothing relevant for: search the other agricultural documents on KP."""
    from agents.tools.advisory import _post_search, build_advisory_search_payload, parse_advisory_response

    data, error = await _post_search(build_advisory_search_payload(query=query))
    if data is None:
        return error
    results = parse_advisory_response(data)[0]
    if not results:
        return NO_RESULTS_MESSAGE
    blocks = [f"**{r.get('title') or 'Document'}**\n```\n{r['text']}\n```" for r in results[:5]]
    return (
        "> Document Search Results for `" + query + "`\n\n"
        + "\n\n----\n\n".join(blocks)
        + "\n\n> Cite the Source line shown in the text you use. If none is about this scheme, say the "
        "information is not available."
    )


async def _search_national_schemes(ctx: RunContext[FarmerContext], query: str, top_k: int) -> str:
    """Scheme guideline search on the Knowledge Provider (POST /search, search_profile scheme)."""
    logger.info("TOOL CALL: search_schemes(query=%r, top_k=%d)", query, top_k)
    payload = _build_scheme_search_payload(query=query, top_k=top_k)
    lf_update_current_observation(
        input={"query": query, "top_k": top_k},
        metadata={
            "tool": "scheme.search",
            "category": SCHEME_SEARCH_CATEGORY,
        },
    )
    search_url = knowledge_provider.search_url()
    if not search_url:
        logger.error("KNOWLEDGE_PROVIDER_URL is not set")
        return "Scheme search service is not configured. Please try again later."
    logger.info("Scheme KP search: query=%r url=%s", query, search_url)

    try:
        data = await knowledge_provider.search(payload)
        results, search_ctx = _parse_scheme_search_response(data)
        status = str(search_ctx.get("status") or "").strip().lower()
        message = str(search_ctx.get("message") or "").strip()

        if status and status != "success" and not results:
            logger.info(
                "TOOL RESULT: search_schemes — KP status=%s message=%r hits=%s",
                status,
                message,
                len(results),
            )
            lf_update_current_observation(
                metadata={
                    "tool": "scheme.search",
                    "category": SCHEME_SEARCH_CATEGORY,
                    "network_status": status or None,
                    "hit_count": 0,
                    "doc_ids": [],
                    "chunk_ids": [],
                    "widened_to_all_schemes": search_ctx.get("widened_to_all_schemes"),
                }
            )
            return message or NO_RESULTS_MESSAGE

        if top_k and top_k > 0:
            results = results[:top_k]

        # Shown in Langfuse tool metadata.
        doc_ids = list(
            dict.fromkeys(str(r.get("doc_id") or "") for r in results if r.get("doc_id"))
        )
        chunk_ids = [str(r.get("chunk_id") or "") for r in results if r.get("chunk_id")]

        lf_update_current_observation(
            metadata={
                "tool": "scheme.search",
                "category": SCHEME_SEARCH_CATEGORY,
                "network_status": status or None,
                "hit_count": len(results),
                "doc_ids": doc_ids,
                "chunk_ids": chunk_ids,
                "chunks": [
                    {
                        "doc_id": r.get("doc_id") or "",
                        "chunk_id": r.get("chunk_id") or "",
                        "scheme_code": r.get("scheme_code") or "",
                        "section": r.get("section") or "",
                        "score": r.get("score"),
                        "source": r.get("source") or "",
                    }
                    for r in results
                ],
                "widened_to_all_schemes": search_ctx.get("widened_to_all_schemes"),
            }
        )
        logger.info(
            "TOOL RESULT: search_schemes — hit_count=%d doc_ids=%s",
            len(results), doc_ids,
        )

        formatted = format_search_results(results, query)
        if results:
            # No resolved scheme any more: offer status checks for the top-ranked hit's scheme.
            formatted += format_status_check_offer(results[0].get("scheme_code"))
        return formatted

    except knowledge_provider.KnowledgeProviderError as e:
        logger.error("Scheme KP search failed: %s", e)
        lf_update_current_observation(
            metadata={"tool": "scheme.search", "error_type": "knowledge_provider"}
        )
        return "Scheme search service is unavailable. Please try again later."

    except httpx.TimeoutException as e:
        logger.error("Scheme KP search timed out: %s", e)
        lf_update_current_observation(
            metadata={"tool": "scheme.search", "error_type": "timeout"}
        )
        return "Scheme search request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error("Scheme KP search request failed: %s", e)
        lf_update_current_observation(
            metadata={"tool": "scheme.search", "error_type": "request_error"}
        )
        return f"Scheme search request failed: {e!s}"

    except Exception as e:
        logger.error("Error searching schemes: %s for query: %s", e, query)
        lf_update_current_observation(
            metadata={"tool": "scheme.search", "error_type": "exception"}
        )
        raise ModelRetry("Error searching schemes, please try again") from e
