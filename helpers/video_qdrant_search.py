"""Qdrant-backed semantic retrieval for eNAM training videos."""
from __future__ import annotations

import re
from typing import Any, Optional

from qdrant_client.models import FieldCondition, Filter, MatchValue
from sentence_transformers import SentenceTransformer

from helpers.scheme_qdrant_search import embed_query, get_embedder, get_qdrant_client


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _hit_to_result(hit: Any) -> dict[str, Any]:
    payload = hit.payload or {}
    return {
        "score": float(hit.score),
        "name": str(payload.get("name") or "Untitled video"),
        "source": str(payload.get("source") or ""),
        "text": str(payload.get("text") or ""),
        "id": payload.get("id"),
        "doc_id": payload.get("doc_id"),
    }


def search_videos(
    query: str,
    collection_name: str,
    top_k: int = 3,
    model: Optional[SentenceTransformer] = None,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Search video transcript payloads in a Qdrant collection.

    The collection must contain ``type=video`` plus ``name``, ``source`` and
    ``text`` payload fields. The vectors must use the same multilingual E5
    embedding model as the query encoder.
    """
    client = client or get_qdrant_client()
    model = model or get_embedder()
    hits = client.query_points(
        collection_name=collection_name,
        query=embed_query(query, model),
        query_filter=Filter(
            must=[FieldCondition(key="type", match=MatchValue(value="video"))]
        ),
        limit=top_k,
        with_payload=True,
    ).points
    return [_hit_to_result(hit) for hit in hits]


def format_video_search_results(results: list[dict[str, Any]], query: str) -> str:
    """Format Qdrant video hits for direct use by the agent."""
    if not results:
        return f"No videos found for `{query}`"

    blocks = []
    for result in results:
        name = result["name"]
        source = result["source"]
        title = f"[{name}]({source})" if source else name
        text = _clean_text(result["text"])
        blocks.append(
            f"**{title}** (score={result['score']:.4f})\n"
            f"```\n{text}\n```"
        )
    return f"> Videos for `{query}`\n\n" + "\n\n----\n\n".join(blocks)
