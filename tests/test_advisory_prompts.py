"""Every language prompt must route crop, pest, livestock and photo questions to search_advisory, search first."""

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
    assert "1. **Search first.**" in flow
    assert "Livestock questions" in flow and "with no `crop`" in flow
    assert "A request to ask which crop" in flow
    assert "The farmer's place comes only from the farmer" in flow
    assert "Never take it from the browser coordinates" in flow
    assert "A request to ask for the farmer's place" in flow
    assert "the very first line of your answer" in flow and "add nothing from your own knowledge" in flow
    assert "call `search_advisory` again with the same `query`" in flow
    assert "**Source: ICAR - Kisan Sarathi**" in flow
    assert "your first reply is only one short question asking which crop" in flow
    assert "`answer_now=true`" in flow and "`crop=\"Wheat\"`, `days_after_sowing=40`" in flow
    assert "never put season names in brackets" in flow
    assert "the farmer decides" in flow
    assert "Your whole reply is only that question" in flow
    assert "never fill the gap from memory" in flow


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_a_missing_crop_is_asked_first_in_the_tool_rows(path):
    asking = [r for r in _rows(path) if "ask which crop first" in r and "`crop`" in r]
    assert len(asking) == 2, f"{path.name}: crop info and pests rows must ask for a missing crop"
    assert any("general knowledge" in r for r in asking)


def test_global_prompt_lets_a_tool_question_stand_alone():
    """A question the farmer must answer first is the whole reply: no source line, no extra follow-up."""
    prompt = (Path(__file__).resolve().parents[1] / "assets/prompts/chat/agrinet_en.md").read_text()
    assert "Exception — asking the farmer" in prompt
    assert "no source citation and no extra follow-up question" in prompt
