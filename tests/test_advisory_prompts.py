"""Every language prompt must route crop advisory questions to search_advisory, search first."""

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


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_no_generic_or_personalised_flow(path):
    text = path.read_text(encoding="utf-8")
    for old in ("advice_type", "generic or personalised", "Generic or personalised", "personalised advice", "ask-first"):
        assert old not in text, f"{path.name}: still mentions {old!r}"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_the_search_first_advisory_flow(path):
    text = path.read_text(encoding="utf-8")
    flow = text.split("### Crop advisory flow", 1)[1].split("\n## ", 1)[0]
    assert "1. **Search first.**" in flow
    assert "Never ask the farmer for their location" in flow
    assert "call `search_advisory` again with the same `query`" in flow
    assert "**Source: ICAR - Kisan Sarathi**" in flow
    assert "your first reply is only one short question asking which crop" in flow
    assert "`answer_now=true`" in flow and "`crop=\"Wheat\"`, `days_after_sowing=40`" in flow


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_a_missing_crop_is_asked_first_in_the_tool_rows(path):
    rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("|") and "`search_pests_diseases`" in l]
    assert any("ask which crop first" in r for r in rows), f"{path.name}: pests row must ask for a missing crop"
    advisory = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("|") and "`search_advisory`" in l]
    assert len(advisory) == 1 and "ask which crop first" in advisory[0] and "general knowledge" in advisory[0]
