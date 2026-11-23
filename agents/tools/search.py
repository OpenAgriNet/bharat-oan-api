"""
Marqo client implementation for vector search + network scheme document search.
"""
import asyncio
import os
import re
from typing import Any, Dict, List, Literal, Optional

import httpx
import marqo
from langfuse import observe
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.imd_advisory import agromet_note
from helpers import knowledge_provider
from helpers.advisory_pending import ADVISORY_FIRST_RETRY, PENDING_RETRY, advisory_not_searched, advisory_question_pending, crop_question_needs_advisory
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.scheme_qdrant_search import (
    NO_RESULTS_MESSAGE,
    _format_page,
    classify_chunk_section,
    format_search_results,
    format_status_check_offer,
)
from helpers.utils import get_logger
from agents.tools.terms import normalize_text_with_glossary

logger = get_logger(__name__)

DocumentType = Literal["video", "document"]

# Langfuse category label for KP scheme searches.
SCHEME_SEARCH_CATEGORY = "knowledge-provider-scheme"


class SearchHit(BaseModel):
    """Individual search hit from elasticsearch"""
    name: str
    text: str
    doc_id: str
    type: str
    source: str
    score: float = Field(alias="_score")
    id: str = Field(alias="_id")

    @property
    def processed_text(self) -> str:
        """Returns the text with cleaned up whitespace and newlines"""
        # Replace multiple newlines with a single line
        cleaned = re.sub(r"\n{2,}", "\n\n", self.text)
        cleaned = re.sub(r"\t+", "\t", cleaned)
        cleaned = normalize_text_with_glossary(cleaned)
        return cleaned

    def __str__(self) -> str:
        if self.type == "document":
            return f"**{self.name}**\n" + "```\n" + self.processed_text + "\n```\n"
        else:
            return f"**[{self.name}]({self.source})**\n" + "```\n" + self.processed_text + "\n```\n"


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


@observe(name="tool:search_schemes", as_type="tool")
async def search_schemes(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for Bharat Vistaar scheme guideline PDFs via the Knowledge Provider
    (POST /search, search_profile scheme).

    Covers every catalog scheme, including PM-KISAN, PMFBY, KCC, AIF, SHC, SMAM and N.B.M.
    Searches across all indexed schemes; each result is labelled with its scheme, document
    and page. For a direct status/grievance request (e.g. "check my PM-KISAN status"), call
    the status tools instead — do not call this first.

    Args:
        query: Natural-language question in English (eligibility, benefits, application process)
        top_k: Maximum number of chunks to return (default: 10)

    Returns:
        Formatted scheme document chunks labelled [scheme | document | page] with relevance scores
    """
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


@observe(name="tool:search_documents", as_type="tool")
async def search_documents(
    ctx: RunContext[FarmerContext],
    query: str, 
    top_k: int = 10, 
) -> str:
    """
    Semantic search for documents. Use this tool to search for relevant documents.
    
    Args:
        query: The search query in *English* (required)
        top_k: Maximum number of results to return (default: 10)
        
    Returns:
        search_results: Formatted list of documents
    """
    if crop_question_needs_advisory(ctx):
        raise ModelRetry(ADVISORY_FIRST_RETRY)
    if advisory_question_pending(ctx):
        raise ModelRetry(PENDING_RETRY)
    try:
        # Initialize Marqo client
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")
        
        index_name = os.getenv('MARQO_INDEX_NAME', 'sunbird-va-index')
        if not index_name:
            raise ValueError("Marqo index name is required")
        
        client = marqo.Client(url=endpoint_url)
        client.config.timeout = 10
        logger.info(f"Searching for '{query}' in index '{index_name}'")

        filter_string = f"type:document"
            
        # Perform search
        search_params = {
            "q": query,
            "limit": top_k,
            "filter_string": "type:document",
            "search_method": "hybrid",
            "hybrid_parameters": {
                "retrievalMethod": "disjunction",
                "rankingMethod": "rrf",
                "alpha": 0.5,
                "rrfK": 60,
            },        
        }
        
        results = client.index(index_name).search(**search_params)['hits']
        
        if len(results) == 0:
            return f"No results found for `{query}`"
        else:            
            search_hits = [SearchHit(**hit) for hit in results]            
            # Convert back to dict format for compatibility
            document_string = '\n\n----\n\n'.join([str(document) for document in search_hits])
            return "> Search Results for `" + query + "`\n\n" + document_string + await agromet_note(ctx, query=query)
    except Exception as e:
        logger.error(f"Error searching documents: {e} for query: {query}")
        raise ModelRetry(f"Error searching documents, please try again")



@observe(name="tool:search_video", as_type="tool")
async def search_video(
    query: str, 
    top_k: int = 10, 
) -> str:
    """
    Semantic search for videos. Use this tool when recommending videos to the farmer.
    
    Args:
        query: The search query in *English* (required)
        top_k: Maximum number of results to return (default: 3)
        
    Returns:
        search_results: Formatted list of videos
    """
    try:
        from helpers.video_qdrant_search import (
            format_video_search_results,
            search_videos as qdrant_search_videos,
        )

        collection_name = os.getenv("QDRANT_VIDEO_COLLECTION_NAME", "video_data_collection")
        if not os.getenv("QDRANT_URL"):
            raise ValueError("QDRANT_URL is required")

        logger.info("Searching videos for %r in Qdrant collection %r", query, collection_name)
        results = await asyncio.to_thread(
            qdrant_search_videos, query, collection_name, top_k
        )
        return format_video_search_results(results, query)
        
    except Exception as e:
        logger.error(f"Error searching videos: {e} for query: {query}")
        raise ModelRetry(f"Error searching videos, please try again")

@observe(name="tool:search_pests_diseases", as_type="tool")
async def search_pests_diseases(
    ctx: RunContext[FarmerContext],
    query: str,
    crop: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for **crop** pests and diseases only (e.g. crop insects, fungal/bacterial diseases of plants).
    Do NOT use for livestock/animal diseases (cattle, buffalo, goat, poultry, etc.) — use search_documents for those.
    
    Args:
        query: The search query in *English* (required)
        crop: The crop in English, for example "Wheat" (required). If the farmer has not said which crop,
            do not call this tool: ask which crop first.
        top_k: Maximum number of results to return (default: 10)
        
    Returns:
        search_results: Formatted list of pests and diseases information
    """
    if not " ".join(str(crop or "").split()):
        raise ModelRetry(
            "The crop is required. If the farmer named a crop, call search_pests_diseases again with `crop`. "
            "If they did not, ask them which crop in one short question, wait for the answer, then search."
        )
    if advisory_not_searched(ctx):
        raise ModelRetry(ADVISORY_FIRST_RETRY)
    if advisory_question_pending(ctx):
        raise ModelRetry(PENDING_RETRY)
    try:
        # Initialize Marqo client
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")
        
        # Use separate index for pests and diseases
        index_name = os.getenv('MARQO_PESTS_DISEASES_INDEX_NAME')
        if not index_name:
            raise ValueError("Marqo pests and diseases index name is required.")
        
        client = marqo.Client(url=endpoint_url)
        client.config.timeout = 10
        logger.info(f"Searching for pests/diseases '{query}' in index '{index_name}'")

        # Perform search
        search_params = {
            "q": query,
            "limit": top_k,
            "search_method": "hybrid",
            "hybrid_parameters": {
                "retrievalMethod": "disjunction",
                "rankingMethod": "rrf",
                "alpha": 0.5,
                "rrfK": 60,
            },        
        }
        
        results = client.index(index_name).search(**search_params)['hits']
        
        if len(results) == 0:
            return f"No pests or diseases information found for `{query}`"
        else:            
            search_hits = [SearchHit(**hit) for hit in results]            
            # Convert back to dict format for compatibility
            document_string = '\n\n----\n\n'.join([str(document) for document in search_hits])
            return "> Pests & Diseases Search Results for `" + query + "`\n\n" + document_string + await agromet_note(ctx, query=query)
    except Exception as e:
        logger.error(f"Error searching pests and diseases: {e} for query: {query}")
        raise ModelRetry(f"Error searching pests and diseases, please try again")
