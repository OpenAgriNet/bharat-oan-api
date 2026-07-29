"""
Marqo client implementation for vector search + network scheme document search.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import httpx
import marqo
from langfuse import observe
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.scheme_qdrant_search import (
    format_qdrant_scheme_codes_for_doc,
    format_scheme_unavailable,
    format_search_results,
    get_builtin_scheme_list,
    query_names_unindexed_scheme,
    resolve_scheme_code,
)
from helpers.utils import get_logger
from agents.tools.terms import normalize_text_with_glossary

logger = get_logger(__name__)

DocumentType = Literal["video", "document"]

SCHEME_AGRI_QDRANT_CATEGORY = "scheme-agri-qdrant"


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


# -----------------------
# Scheme document search (BAP /search, category scheme-agri-qdrant)
# Same Beckn shape as SMAM / scheme_info: request.get_payload() + Pydantic response.
# -----------------------
class SchemeDocDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: Optional[str] = None
    name: Optional[str] = None
    short_desc: Optional[str] = None
    long_desc: Optional[str] = None


class SchemeDocTagEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    descriptor: SchemeDocDescriptor
    value: str
    display: Optional[bool] = None


class SchemeDocTagGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")
    descriptor: SchemeDocDescriptor
    list: List[SchemeDocTagEntry] = Field(default_factory=list)
    display: Optional[bool] = None


class SchemeDocItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    descriptor: SchemeDocDescriptor
    tags: List[SchemeDocTagGroup] = Field(default_factory=list)


class SchemeDocProvider(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    descriptor: Optional[SchemeDocDescriptor] = None
    items: List[SchemeDocItem] = Field(default_factory=list)


class SchemeDocCatalog(BaseModel):
    model_config = ConfigDict(extra="ignore")
    descriptor: Optional[SchemeDocDescriptor] = None
    tags: Optional[List[SchemeDocTagGroup]] = None
    providers: List[SchemeDocProvider] = Field(default_factory=list)


class SchemeDocMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    catalog: SchemeDocCatalog


class SchemeDocResponseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    context: Optional[Dict[str, Any]] = None
    message: SchemeDocMessage


def _flatten_tag_values(tag_groups: Optional[List[SchemeDocTagGroup]]) -> Dict[str, str]:
    """Same pattern as SMAM `_flatten_catalog_tag_values`: code → value."""
    merged: Dict[str, str] = {}
    if not tag_groups:
        return merged
    for grp in tag_groups:
        for ent in grp.list:
            key = (ent.descriptor.code or "").strip().lower().replace("-", "_")
            if key:
                merged[key] = ent.value
    return merged


class SchemeDocApiResponse(BaseModel):
    """BAP client JSON: optional `context` + `responses[].message.catalog`."""

    model_config = ConfigDict(extra="ignore")
    context: Optional[Dict[str, Any]] = None
    responses: List[SchemeDocResponseItem] = Field(default_factory=list)

    def search_context(self) -> Dict[str, str]:
        for rsp in self.responses:
            ctx = _flatten_tag_values(rsp.message.catalog.tags)
            if ctx:
                return ctx
        return {}

    def chunk_results(self) -> List[Dict[str, Any]]:
        """Map providers/items/chunk-details tags into format_search_results rows."""
        results: List[Dict[str, Any]] = []
        for rsp in self.responses:
            for provider in rsp.message.catalog.providers:
                for item in provider.items:
                    details = _flatten_tag_values(item.tags)
                    text = (details.get("text") or item.descriptor.long_desc or "").strip()
                    if not text:
                        continue
                    try:
                        score = float(details.get("score") or 0)
                    except (TypeError, ValueError):
                        score = 0.0
                    results.append(
                        {
                            "score": score,
                            "scheme_code": (
                                details.get("scheme_code") or item.descriptor.code or ""
                            ).strip(),
                            "scheme_name": (
                                details.get("scheme_name") or item.descriptor.name or ""
                            ).strip(),
                            "text": text,
                            "doc_id": (details.get("doc_id") or "").strip(),
                            "chunk_id": (details.get("chunk_id") or item.id or "").strip(),
                            "section": (details.get("section") or "other").strip().lower(),
                        }
                    )
        return results


class SchemeDocSearchRequest(BaseModel):
    """Beckn /search request for scheme-agri-qdrant (mirrors SchemeRequest / SmamStatusRequest)."""

    query: str
    scheme_code: Optional[str] = None
    session_id: str = ""
    question_id: str = ""

    def get_payload(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        item_descriptor: Dict[str, str] = {"name": self.query}
        if self.scheme_code:
            item_descriptor["code"] = self.scheme_code

        return {
            "context": {
                "domain": "schemes:vistaar",
                "action": "search",
                "version": "1.1.0",
                "bap_id": os.getenv("BAP_ID"),
                "bap_uri": os.getenv("BAP_URI"),
                "bpp_id": os.getenv("BPP_ID"),
                "bpp_uri": os.getenv("BPP_URI"),
                "transaction_id": str(uuid.uuid4()),
                "message_id": str(uuid.uuid4()),
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "ttl": "PT10M",
                "location": {
                    "country": {"code": "IND"},
                    "city": {"code": "*"},
                },
                "tags": {
                    "session_id": self.session_id,
                    "question_id": self.question_id,
                },
            },
            "message": {
                "intent": {
                    "category": {
                        "descriptor": {
                            "code": SCHEME_AGRI_QDRANT_CATEGORY,
                            "name": SCHEME_AGRI_QDRANT_CATEGORY,
                        }
                    },
                    "item": {
                        "descriptor": item_descriptor,
                    },
                }
            },
        }


@observe(name="tool:search_schemes", as_type="tool")
async def search_schemes(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for Bharat Vistaar scheme guideline PDFs via the Vistaar network layer
    (BAP /search, category scheme-agri-qdrant).

    PLACEHOLDER_SCHEME_CODES

    Do NOT use for legacy integrated schemes handled by get_scheme_info
    (pmkisan, pmfby, kcc, pmksy, shc, sathi, pmasha, aif, smam, pdmc, nfsm, rad, ffs, nbhm).

    Args:
        query: Natural-language question in English (eligibility, benefits, application process)
        top_k: Maximum number of chunks to return (default: 10)

    Returns:
        Formatted scheme document chunks with scheme names and relevance scores
    """
    scheme_list = get_builtin_scheme_list()
    if query_names_unindexed_scheme(query, scheme_list):
        logger.info("Scheme not in indexed list for query %r", query)
        return format_scheme_unavailable(query)

    scheme_code = resolve_scheme_code(query, scheme_list)
    payload = SchemeDocSearchRequest(
        query=query,
        scheme_code=scheme_code,
        session_id=ctx.deps.session_id,
        question_id=ctx.deps.question_id,
    ).get_payload()
    transaction_id = payload.get("context", {}).get("transaction_id")
    lf_update_current_observation(
        input={"query": query, "top_k": top_k, "scheme_code": scheme_code},
        metadata={
            "tool": "scheme.search",
            "category": SCHEME_AGRI_QDRANT_CATEGORY,
            "transaction_id": transaction_id,
            "resolved_scheme_code": scheme_code,
        },
    )

    bap_endpoint = os.getenv("BAP_ENDPOINT")
    if not bap_endpoint:
        logger.error("BAP_ENDPOINT is not set")
        return "Scheme search service is not configured. Please try again later."

    search_url = bap_endpoint.rstrip("/") + "/search"
    logger.info(
        "Scheme network search: query=%r scheme_code=%s url=%s",
        query,
        scheme_code,
        search_url,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                search_url,
                json=payload,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )

        if response.status_code != 200:
            logger.error(
                "Scheme network search returned status %s — %s",
                response.status_code,
                (response.text or "")[:500],
            )
            lf_update_current_observation(
                metadata={
                    "tool": "scheme.search",
                    "http_status": int(response.status_code),
                }
            )
            return "Scheme search service is unavailable. Please try again later."

        parsed = SchemeDocApiResponse.model_validate(response.json())
        search_ctx = parsed.search_context()
        results = parsed.chunk_results()
        status = (search_ctx.get("status") or "").strip().lower()
        message = (search_ctx.get("message") or "").strip()
        resolved = (search_ctx.get("resolved_scheme_code") or "").strip()

        lf_update_current_observation(
            metadata={
                "tool": "scheme.search",
                "category": SCHEME_AGRI_QDRANT_CATEGORY,
                "transaction_id": transaction_id,
                "network_status": status or None,
                "resolved_scheme_code": resolved or scheme_code,
                "hit_count": len(results),
                "providers_source": search_ctx.get("source"),
                "search_backend": search_ctx.get("search_backend"),
            }
        )

        if status and status != "success" and not results:
            logger.info(
                "Scheme network search non-success status=%s message=%r",
                status,
                message,
            )
            return message or format_scheme_unavailable(query)

        if top_k and top_k > 0:
            results = results[:top_k]

        return format_search_results(results, query, scheme_list)

    except httpx.TimeoutException as e:
        logger.error("Scheme network search timed out: %s", e)
        lf_update_current_observation(
            metadata={"tool": "scheme.search", "error_type": "timeout"}
        )
        return "Scheme search request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error("Scheme network search request failed: %s", e)
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


if search_schemes.__doc__:
    search_schemes.__doc__ = search_schemes.__doc__.replace(
        "PLACEHOLDER_SCHEME_CODES",
        f"Available Qdrant scheme codes: {format_qdrant_scheme_codes_for_doc()}.",
    )


@observe(name="tool:search_documents", as_type="tool")
async def search_documents(
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
            return "> Search Results for `" + query + "`\n\n" + document_string
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
    query: str, 
    top_k: int = 10, 
) -> str:
    """
    Semantic search for **crop** pests and diseases only (e.g. crop insects, fungal/bacterial diseases of plants).
    Do NOT use for livestock/animal diseases (cattle, buffalo, goat, poultry, etc.) — use search_documents for those.
    
    Args:
        query: The search query in *English* (required)
        top_k: Maximum number of results to return (default: 10)
        
    Returns:
        search_results: Formatted list of pests and diseases information
    """
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
            return "> Pests & Diseases Search Results for `" + query + "`\n\n" + document_string
    except Exception as e:
        logger.error(f"Error searching pests and diseases: {e} for query: {query}")
        raise ModelRetry(f"Error searching pests and diseases, please try again")
