"""Typed shapes for the scheme master catalog snapshot."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CatalogScheme(BaseModel):
    """One row from public.master_catalog (live-visible subset in the snapshot)."""

    code: str
    name: str
    tool_name: str = "search_schemes"
    content_type: str = "scheme"
    doc_id: Optional[str] = None
    prompt_snippet: Optional[str] = None
    status: str = "live"
    workflow_id: Optional[str] = None
    instance: Optional[str] = None
    # Optional aliases (not on Postgres v1; accepted if publisher includes them)
    aliases: list[str] = Field(default_factory=list)

    def to_scheme_list_item(self) -> dict[str, Any]:
        """Shape expected by resolve_scheme_code / search_schemes helpers."""
        aliases = list(self.aliases)
        if self.prompt_snippet:
            # Soft-split common comma/slash aliases from snippet text.
            for part in self.prompt_snippet.replace("/", ",").split(","):
                cleaned = part.strip()
                if cleaned and cleaned not in aliases:
                    aliases.append(cleaned)
        return {
            "scheme_code": self.code,
            "scheme_name": self.name,
            "scheme_aliases": aliases,
            "tool_name": self.tool_name,
            "prompt_snippet": self.prompt_snippet,
        }


class CatalogSnapshot(BaseModel):
    """Versioned catalog payload cached in Redis / process memory."""

    catalog_version: int = 0
    updated_at: Optional[str] = None
    schemes: list[CatalogScheme] = Field(default_factory=list)
    source: str = "empty"  # api | redis | builtin | empty

    @property
    def version(self) -> int:
        return self.catalog_version

    def vector_schemes(self) -> list[CatalogScheme]:
        vector_tools = {"search_schemes", "qdrant", "both"}
        out: list[CatalogScheme] = []
        for s in self.schemes:
            tn = (s.tool_name or "search_schemes").strip()
            if tn in vector_tools or tn.endswith("search_schemes"):
                out.append(s)
        return out

    def legacy_schemes(self) -> list[CatalogScheme]:
        legacy_tools = {"get_scheme_info", "legacy", "scheme_info"}
        return [
            s
            for s in self.schemes
            if (s.tool_name or "").strip() in legacy_tools
        ]

    def scheme_list_for_search(self) -> list[dict[str, Any]]:
        """Active vector-scheme list for allow-list / resolve."""
        return [s.to_scheme_list_item() for s in self.vector_schemes()]

    def allowed_vector_codes(self) -> frozenset[str]:
        return frozenset(s.code for s in self.vector_schemes())
