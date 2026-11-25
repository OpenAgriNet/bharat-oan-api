"""Every language prompt must route crop, pest, livestock and photo questions to search_advisory, search first."""

import re
from pathlib import Path

import pytest

PROMPTS = sorted((Path(__file__).resolve().parent.parent / "assets" / "prompts" / "skills" / "crop" / "advice").glob("advice_*.md"))
REMOVED_TOOLS = ("search_documents", "search_pests_diseases", "search_terms", "analyze_crop_image")


def _render(path, context=None):
    from agents.skill_catalog import get_catalog

    return get_catalog().render_skill("crop.advice", path.stem.removeprefix("advice_"), context or {})


def _rows(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|") and "`search_advisory`" in line]


def test_all_thirteen_language_prompts_are_covered():
    assert len(PROMPTS) == 13


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_every_tool_row_uses_search_advisory(path):
    rows = _rows(path)
    assert len(rows) == 4, f"{path.name}: crop info, pests, livestock and photo rows must all use search_advisory"
    for row in rows:
        assert len([c for c in row.strip().strip("|").split("|")]) == 4, f"{path.name}: tool row must have 4 columns"
    assert any("`image_url`" in row for row in rows)
    assert any("Leave `crop` empty" in row for row in rows)


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_no_removed_tool_is_mentioned(path):
    text = _render(path)
    for tool in REMOVED_TOOLS:
        assert tool not in text, f"{path.name}: still mentions {tool}"
    assert "### Document search" not in text
    assert "Check documents" not in text


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_no_generic_or_personalised_flow(path):
    text = _render(path)
    for old in ("advice_type", "generic or personalised", "Generic or personalised", "personalised advice", "ask-first"):
        assert old not in text, f"{path.name}: still mentions {old!r}"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_the_search_first_advisory_flow(path):
    flow = _render(path).split("### Crop advisory flow", 1)[1]
    assert "1. **Search first, answer first.**" in flow
    assert "not even the crop" in flow and "If the farmer has not said which crop, search without `crop`" in flow
    assert "Livestock questions" in flow and "with no `crop`" in flow
    assert "end your answer with that ONE follow-up question, after the Source line" in flow
    assert "**Which crop**" in flow and "**Another season**" in flow
    assert "**The season, the days since sowing and/or the place**" in flow and "in that one question" in flow
    assert "Ask each follow-up only once" in flow
    assert "The farmer's place comes only from the farmer" in flow
    assert "Never take it from the browser coordinates" in flow
    assert "the very first line of your answer" in flow and "add nothing from your own knowledge" in flow
    assert "call `search_advisory` again with the same `query`" in flow
    assert "document title from the tool’s `Cite as` line" in flow
    assert "ICAR" not in flow
    assert "`crop=\"Wheat\"`, `days_after_sowing=40`" in flow
    assert "never put season names in brackets" in flow
    assert "the farmer decides" in flow
    assert "never fill the gap from memory" in flow
    for gone in ("answer_now", "Your whole reply is only that question", "your first reply is only one short question"):
        assert gone not in flow


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_a_missing_crop_is_searched_without_a_crop_in_the_tool_rows(path):
    rows = _rows(path)
    searching = [r for r in rows if "search without `crop`" in r]
    assert len(searching) == 2, f"{path.name}: crop info and pests rows must search when no crop is named"
    assert any("general knowledge" in r for r in searching)
    assert not any("ask which crop first" in r for r in rows)


@pytest.mark.parametrize("path", sorted((Path(__file__).resolve().parents[1] / "assets/prompts/chat").glob("agrinet_*.md")), ids=lambda p: p.stem)
def test_global_prompt_always_answers_first(path):
    """No question-only replies: answer, then source, then the one follow-up question (in every language)."""
    prompt = path.read_text()
    # the removed "Exception — asking the farmer" bullet (bold label with an em dash), in any language
    assert not re.search(r"^- \*\*[^*\n]+ — [^*\n]+:\*\*", prompt, re.MULTILINE)
    if path.stem == "agrinet_en":
        assert "Answer first, then source citation on its own line, then the follow-up question last" in prompt
