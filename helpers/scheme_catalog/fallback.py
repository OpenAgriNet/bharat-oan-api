"""Builtin catalog built from the hard-coded Qdrant scheme registry."""

from __future__ import annotations

from helpers.scheme_catalog.models import CatalogScheme, CatalogSnapshot
from helpers.scheme_qdrant_search import get_builtin_scheme_list


def builtin_catalog_snapshot() -> CatalogSnapshot:
    """Fallback when the master catalog is disabled or unreachable."""
    schemes = [
        CatalogScheme(
            code=item["scheme_code"],
            name=item["scheme_name"],
            tool_name="search_schemes",
            aliases=list(item.get("scheme_aliases") or []),
            status="live",
        )
        for item in get_builtin_scheme_list()
    ]
    return CatalogSnapshot(
        catalog_version=0,
        schemes=schemes,
        source="builtin",
    )
