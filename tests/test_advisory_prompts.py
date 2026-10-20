"""Every language prompt must route crop advisory questions to search_advisory."""

import re
from pathlib import Path

import pytest

PROMPTS = sorted((Path(__file__).resolve().parent.parent / "assets" / "prompts").glob("agrinet_*.md"))


def test_all_thirteen_language_prompts_are_covered():
    assert len(PROMPTS) == 13


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_a_search_advisory_row_in_the_tool_table(path):
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|") and "`search_advisory`" in line]
    assert len(rows) == 1, f"{path.name}: expected exactly one tool-table row for search_advisory"
    cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert len(cells) == 4, f"{path.name}: tool row must have 4 columns"
    assert "`search_documents`" in cells[3], "row must name search_documents as the fallback"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_existing_document_and_pest_rows_are_untouched(path):
    text = path.read_text(encoding="utf-8")
    assert len(re.findall(r"^\|[^\n]*`search_documents`[^\n]*\|$", text, flags=re.M)) >= 2
    assert "`search_pests_diseases`" in text
