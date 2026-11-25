"""General documents and scheme guidelines through Knowledge Provider."""
from typing import Any, Dict, List, Optional

import httpx
from langfuse import observe
from pydantic_ai import ModelRetry

from helpers import knowledge_provider
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.scheme_qdrant_search import (
    NO_RESULTS_MESSAGE,
    _format_page,
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
        "scheme_code": str(hit.get("scheme_code") or hit.get("code") or "").strip().lower(),
        "scheme_name": str(hit.get("scheme_name") or hit.get("name_en") or hit.get("name") or "").strip(),
        "text": text,
        "doc_id": str(hit.get("doc_id") or "").strip(),
        "chunk_id": str(hit.get("chunk_id") or hit.get("_id") or "").strip(),
        # KP's section_type (eligibility, exclusion, application, support, other) is the old network "section".
        "section": str(hit.get("section_type") or "").strip().lower() or "other",
        # KP's origin is the intake channel, not a citation; format_search_results falls back to its label.
        "source": "",
        "doc_title": str(hit.get("title_en") or hit.get("name_en") or hit.get("name") or hit.get("filename") or "").strip(),
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


async def _fetch_documents(payload: dict, tool: str, category: str) -> tuple[dict, str]:
    lf_update_current_observation(input=payload, metadata={"tool": tool, "category": category})
    if not knowledge_provider.search_url():
        logger.error("KNOWLEDGE_PROVIDER_URL is not set")
        return {}, "Document search service is not configured. Please try again later."

    try:
        return await knowledge_provider.search(payload), ""
    except (knowledge_provider.KnowledgeProviderError, httpx.HTTPError) as exc:
        logger.warning("Knowledge Provider document search failed: %s", exc)
        lf_update_current_observation(metadata={"tool": tool, "error_type": type(exc).__name__})
        return {}, "Document search service is unavailable. Please try again later."


def _search_payload(query: str, top_k: int) -> dict:
    query = query.strip()
    if not query:
        raise ModelRetry("Provide a non-empty document search query.")
    return {"query": query, "top_k": min(max(top_k, 1), 50)}


@observe(name="tool:search_documents", as_type="tool")
async def search_documents(query: str, top_k: int = 10) -> str:
    """Search agricultural manuals, guides and general documents.

    Args:
        query: A short English search question naming the document or subject.
        top_k: Maximum passage count, from 1 to 50.
    """
    data, error = await _fetch_documents(_search_payload(query, top_k), "documents.search", "knowledge-provider")
    if error:
        return error
    hits = [
        hit for hit in data.get("hits") or []
        if isinstance(hit, dict) and hit.get("text")
        and (hit.get("document_kind") or hit.get("type")) != "video"
    ]
    lf_update_current_observation(metadata={"tool": "documents.search", "hit_count": len(hits)})
    return _format_documents(hits)


@observe(name="tool:search_schemes", as_type="tool")
async def search_schemes(query: str, top_k: int = 10, scheme_code: Optional[str] = None) -> str:
    """Search national scheme guidelines, including eligibility, benefits and applications.

    Args:
        query: A short English search question containing the scheme name or acronym.
        top_k: Maximum passage count, from 1 to 50.
        scheme_code: Optional exact code when the scheme catalog code is known.
    """
    payload = {**_search_payload(query, top_k), "document_kind": "scheme"}
    scheme_code = (scheme_code or "").strip().lower()
    if scheme_code:
        payload["document_code"] = scheme_code
    data, error = await _fetch_documents(payload, "scheme.search", SCHEME_SEARCH_CATEGORY)
    if error:
        return error
    return _format_schemes(data, payload["query"], scheme_code)


def _format_schemes(data: dict, query: str, scheme_code: str) -> str:
    results, search_ctx = _parse_scheme_search_response(data)
    resolved_code = scheme_code or search_ctx.get("resolved_scheme_code", "").lower()
    if resolved_code:
        results = [hit for hit in results if hit["scheme_code"] == resolved_code]
    status = search_ctx.get("status", "").lower()
    if status and status != "success":
        results = []

    lf_update_current_observation(metadata={
        "tool": "scheme.search",
        "network_status": status or None,
        "hit_count": len(results),
        "doc_ids": list(dict.fromkeys(r["doc_id"] for r in results if r["doc_id"])),
        "chunk_ids": [r["chunk_id"] for r in results if r["chunk_id"]],
        "chunks": [
            {key: result[key] for key in ("doc_id", "chunk_id", "scheme_code", "section", "score", "source")}
            for result in results
        ],
    })
    if not results:
        message = search_ctx.get("message") if status == "scheme_unavailable" else None
        return message or NO_RESULTS_MESSAGE
    formatted = format_search_results(results, query)
    if resolved_code:
        formatted += format_status_check_offer(resolved_code)
    return formatted


def _format_documents(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "No relevant documents found for this query."
    blocks = []
    for hit in hits:
        title = str(hit.get("title_en") or hit.get("name_en") or hit.get("name") or hit.get("filename") or "Document")
        section = hit.get("section")
        page = _format_page(hit.get("page_start"), hit.get("page_end"))
        reference = " | ".join(str(part) for part in (title, section, f"p. {page}" if page else "") if part)
        blocks.append(f"**Source: {reference}**\n\n{hit['text']}")
    return "\n\n----\n\n".join(blocks)
