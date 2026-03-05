"""
Marqo client implementation for vector search.
"""
import os
import re
import marqo
from typing import Optional, Literal
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from helpers.utils import get_logger
from synthetic.tools.terms import normalize_text_with_glossary

logger = get_logger(__name__)

DocumentType = Literal['video', 'document']

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
        cleaned = re.sub(r'\n{2,}', '\n\n', self.text)
        cleaned = re.sub(r'\t+', '\t', cleaned)
        cleaned = normalize_text_with_glossary(cleaned)
        return cleaned

    def __str__(self) -> str:
        if self.type == 'document':
            return f"**{self.name}**\n" + "```\n" + self.processed_text +  "\n```\n"
        else:
            return f"**[{self.name}]({self.source})**\n" + "```\n" + self.processed_text + "\n```\n"

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
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")

        index_name = os.getenv('MARQO_INDEX_NAME', 'sunbird-va-index')
        if not index_name:
            raise ValueError("Marqo index name is required")

        client = marqo.Client(url=endpoint_url)
        client.config.timeout = 10
        logger.info(f"Searching for '{query}' in index '{index_name}'")

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
            document_string = '\n\n----\n\n'.join([str(document) for document in search_hits])
            return "> Search Results for `" + query + "`\n\n" + document_string
    except Exception as e:
        logger.error(f"Error searching documents: {e} for query: {query}")
        raise ModelRetry(f"Error searching documents, please try again")


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
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")

        index_name = os.getenv('MARQO_PESTS_DISEASES_INDEX_NAME')
        if not index_name:
            raise ValueError("Marqo pests and diseases index name is required.")

        client = marqo.Client(url=endpoint_url)
        client.config.timeout = 10
        logger.info(f"Searching for pests/diseases '{query}' in index '{index_name}'")

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
            document_string = '\n\n----\n\n'.join([str(document) for document in search_hits])
            return "> Pests & Diseases Search Results for `" + query + "`\n\n" + document_string
    except Exception as e:
        logger.error(f"Error searching pests and diseases: {e} for query: {query}")
        raise ModelRetry(f"Error searching pests and diseases, please try again")
