"""Build Jinja context for agrinet prompts from a catalog snapshot."""

from __future__ import annotations

from typing import Any

from helpers.scheme_catalog.models import CatalogSnapshot


# Fixed dual-routing defaults (catalog tools do not override these product rules).
_DEFAULT_PKVY_TOOL = "search_schemes"
_DEFAULT_NBM_TOOL = "get_scheme_info"


def build_prompt_context(snapshot: CatalogSnapshot) -> dict[str, Any]:
    """Jinja variables injected into every agrinet_*.md template."""
    vector = snapshot.vector_schemes()
    legacy = snapshot.legacy_schemes()

    vector_lines = []
    identifier_lines = []
    for s in vector:
        snip = f" — {s.prompt_snippet}" if s.prompt_snippet else ""
        vector_lines.append(f"- **{s.name}** ({s.code}){snip}")
        alias_bit = ""
        if s.aliases:
            alias_bit = " / " + " / ".join(s.aliases[:6])
        elif s.prompt_snippet:
            alias_bit = f" / {s.prompt_snippet}"
        identifier_lines.append(f"- `{s.code}` / {s.name.lower()}{alias_bit}")

    # Flat available list: prefer vector + any legacy in snapshot; if no legacy
    # rows in catalog, leave available_schemes_flat to template static merge logic
    # via counts only.
    seen_codes: set[str] = set()
    flat_lines: list[str] = []
    for s in legacy + vector:
        key = s.code.lower()
        if key in seen_codes:
            continue
        seen_codes.add(key)
        flat_lines.append(f"- {s.name} ({s.code})")

    return {
        "catalog_version": snapshot.catalog_version,
        "catalog_source": snapshot.source,
        "vector_schemes": [s.model_dump() for s in vector],
        "legacy_schemes": [s.model_dump() for s in legacy],
        "vector_scheme_count": len(vector),
        "legacy_scheme_count": len(legacy) if legacy else 16,
        "vector_schemes_block": "\n".join(vector_lines) if vector_lines else "- (none currently live)",
        "vector_identifiers_block": "\n".join(identifier_lines)
        if identifier_lines
        else "- (none currently live)",
        "available_schemes_flat": "\n".join(flat_lines) if flat_lines else "",
        "pkvy_tool": _DEFAULT_PKVY_TOOL,
        "nbm_tool": _DEFAULT_NBM_TOOL,
    }
