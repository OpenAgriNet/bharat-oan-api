"""Every language prompt must route crop advisory questions to search_advisory."""

import re
from pathlib import Path

import pytest

PROMPTS = sorted((Path(__file__).resolve().parent.parent / "assets" / "prompts"/ "skills" / "crop" / "advice").glob("advice_*.md"))


def test_all_thirteen_language_prompts_are_covered():
    assert len(PROMPTS) == 13


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_a_search_advisory_row_in_the_tool_table(path):
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|") and "`search_advisory`" in line]
    assert len(rows) == 2, f"{path.name}: crop information and advisory rows must both use ICAR first"
    cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert len(cells) == 4, f"{path.name}: tool row must have 4 columns"
    assert "ICAR" in cells[3], "row must explain ICAR first, then document search"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_existing_document_and_pest_rows_are_untouched(path):
    text = path.read_text(encoding="utf-8")
    assert len(re.findall(r"^\|[^\n]*`search_documents`[^\n]*\|$", text, flags=re.M)) >= 2
    assert "`search_pests_diseases`" in text


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_source_order_and_fallback_are_consistent_in_every_language(path):
    from agents.skill_catalog import get_catalog
    language = path.stem.removeprefix("advice_")
    text = get_catalog().render_skill("crop.advice", language, {})
    assert text.count("### Crop advisory flow") == 1
    assert "Check documents even when ICAR found an advisory" in text
    assert "do not run these two searches in parallel" in text
    assert "Prefer relevant **ICAR - Kisan Sarathi** guidance" in text
    assert "If document search fails but ICAR is relevant, answer from ICAR" in text
    assert "If ICAR has no relevant result, answer from relevant documents" in text
    assert "If it finds no advisory, fall back" not in text
    assert "Nothing found: fall back" not in text
    assert "Generic or personalised: always the first reply" in text
    assert "### Document search" not in text  # Loaded by the manifest dependency, not copied.
