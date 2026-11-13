"""Every language prompt must route crop advisory questions to search_advisory, search first."""

import re
from pathlib import Path

import pytest

def _advice(icar_path):
    """crop.advice prompt in the same language; ICAR rows live in crop.icar so production can switch them off."""
    return icar_path.parent.parent / "advice" / icar_path.name.replace("icar_", "advice_")


PROMPTS = sorted((Path(__file__).resolve().parent.parent / "assets" / "prompts" / "skills" / "crop" / "icar").glob("icar_*.md"))


def _render(path, context=None):
    from agents.skill_catalog import get_catalog

    return get_catalog().render_skill("crop.icar", path.stem.removeprefix("icar_"), context or {})


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
    text = _advice(path).read_text(encoding="utf-8")
    assert len(re.findall(r"^\|[^\n]*`search_documents`[^\n]*\|$", text, flags=re.M)) >= 2
    assert "`search_pests_diseases`" in text


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_source_order_and_fallback_are_consistent_in_every_language(path):
    text = _render(path)
    assert text.count("### Crop advisory flow") == 1
    assert "Check documents even when ICAR found an advisory" in text
    assert "do not run these two searches in parallel" in text
    assert "Prefer relevant **ICAR - Kisan Sarathi** guidance" in text
    assert "If document search fails but ICAR is relevant, answer from ICAR" in text
    assert "If ICAR has no relevant result, answer from relevant documents" in text
    assert "If it finds no advisory, fall back" not in text
    assert "Nothing found: fall back" not in text
    assert "### Document search" not in text  # Loaded by the manifest dependency, not copied.


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_no_generic_or_personalised_flow(path):
    text = _render(path) + path.read_text(encoding="utf-8")
    for old in ("advice_type", "generic or personalised", "Generic or personalised", "personalised advice", "ask-first"):
        assert old not in text, f"{path.name}: still mentions {old!r}"


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_prompt_has_the_search_first_advisory_flow(path):
    flow = _render(path).split("### Crop advisory flow", 1)[1]
    assert "1. **Search first.**" in flow
    assert "The farmer's place comes only from the farmer" in flow
    assert "Never take it from the browser coordinates" in flow
    assert "A request to ask for the farmer's place" in flow
    assert "the very first line of your answer" in flow and "add nothing from your own knowledge" in flow
    assert "pass it as `location` so the tool can note it" not in flow
    assert "call `search_advisory` again with the same `query`" in flow
    assert "**Source: ICAR - Kisan Sarathi**" in flow
    assert "your first reply is only one short question asking which crop" in flow
    assert "`answer_now=true`" in flow and "`crop=\"Wheat\"`, `days_after_sowing=40`" in flow
    assert "never put season names in brackets" in flow
    assert "the farmer decides" in flow
    assert "Your whole reply is only that question" in flow
    assert "not when it asked you to put a question to the farmer" in flow


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.stem)
def test_a_missing_crop_is_asked_first_in_the_tool_rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    advice = _advice(path).read_text(encoding="utf-8").splitlines()
    pests = [l for l in advice if l.startswith("|") and "`search_pests_diseases`" in l and "`search_advisory`" not in l]
    assert any("ask which crop first" in r and "`crop`" in r for r in pests), f"{path.name}: pests row must ask for a missing crop"
    advisory = [l for l in lines if l.startswith("|") and "`search_advisory`" in l and "ask which crop first" in l]
    assert len(advisory) == 1 and "general knowledge" in advisory[0]


def test_global_prompt_lets_a_tool_question_stand_alone():
    """A question the farmer must answer first is the whole reply: no source line, no extra follow-up."""
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[1] / "assets/prompts/chat/agrinet_en.md").read_text()
    assert "Exception — asking the farmer" in prompt
    assert "no source citation and no extra follow-up question" in prompt
