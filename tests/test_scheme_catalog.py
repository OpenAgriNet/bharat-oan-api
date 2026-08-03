"""Unit tests for scheme master-catalog snapshot → prompt context."""

from helpers.scheme_catalog.models import CatalogScheme, CatalogSnapshot
from helpers.scheme_catalog.render import build_prompt_context


def test_build_prompt_context_includes_vector_schemes():
    snap = CatalogSnapshot(
        catalog_version=7,
        schemes=[
            CatalogScheme(
                code="new-scheme",
                name="New Scheme",
                tool_name="search_schemes",
                prompt_snippet="NS, new scheme",
            ),
            CatalogScheme(
                code="pmkisan",
                name="PM Kisan",
                tool_name="get_scheme_info",
            ),
        ],
        source="api",
    )
    ctx = build_prompt_context(snap)
    assert ctx["catalog_version"] == 7
    assert ctx["vector_scheme_count"] == 1
    assert "new-scheme" in ctx["vector_schemes_block"]
    assert "New Scheme" in ctx["vector_schemes_block"]
    list_items = snap.scheme_list_for_search()
    assert list_items[0]["scheme_code"] == "new-scheme"
    assert "NS" in list_items[0]["scheme_aliases"] or "new scheme" in list_items[0]["scheme_aliases"]


def test_vector_vs_legacy_split():
    snap = CatalogSnapshot(
        catalog_version=1,
        schemes=[
            CatalogScheme(code="mif", name="MIF", tool_name="search_schemes"),
            CatalogScheme(code="nbm", name="NBM", tool_name="get_scheme_info"),
        ],
    )
    assert [s.code for s in snap.vector_schemes()] == ["mif"]
    assert [s.code for s in snap.legacy_schemes()] == ["nbm"]
