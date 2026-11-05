"""While search_advisory has a question for the farmer pending, other crop searches must wait."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from agents.tools import advisory as sa
from agents.tools import search as search_tools
from helpers.advisory_pending import PENDING_MARKER, advisory_question_pending

QUESTION = f"{PENDING_MARKER}\n> Advisories were found ... Ask the farmer ONE short question"
ADVICE = "> Advisory Search Results for `q`\n\n**T**\n```\nAdvisory text\n```"


def _user(text="wheat aphids"):
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _advisory_result(content):
    return [
        ModelResponse(parts=[ToolCallPart(tool_name="search_advisory", args={})]),
        ModelRequest(parts=[ToolReturnPart(tool_name="search_advisory", tool_call_id="c1", content=content)]),
    ]


def _ctx(messages):
    return SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"), messages=messages)


class TestDetection:
    def test_a_pending_question_in_this_turn_is_detected(self):
        assert advisory_question_pending(_ctx([_user()] + _advisory_result(QUESTION)))

    def test_advice_is_not_a_pending_question(self):
        assert not advisory_question_pending(_ctx([_user()] + _advisory_result(ADVICE)))

    def test_a_question_from_an_earlier_turn_does_not_count(self):
        history = [_user()] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user("Rabi, 40 days"),
        ]
        assert not advisory_question_pending(_ctx(history))

    def test_the_marker_from_another_tool_does_not_count(self):
        other = ModelRequest(parts=[ToolReturnPart(tool_name="search_documents", tool_call_id="c", content=QUESTION)])
        assert not advisory_question_pending(_ctx([_user(), other]))

    @pytest.mark.parametrize("messages", [None, []])
    def test_no_history_means_not_pending(self, messages):
        assert not advisory_question_pending(_ctx(messages))


@pytest.fixture
def marqo_stub(monkeypatch):
    class Index:
        def search(self, **_):
            return {"hits": []}

    class Client:
        def __init__(self, url=None):
            self.config = SimpleNamespace(timeout=None)

        def index(self, _name):
            return Index()

    monkeypatch.setenv("MARQO_ENDPOINT_URL", "http://marqo.test")
    monkeypatch.setenv("MARQO_INDEX_NAME", "idx")
    monkeypatch.setenv("MARQO_PESTS_DISEASES_INDEX_NAME", "pests-idx")
    monkeypatch.setattr(search_tools.marqo, "Client", Client)


class TestOtherCropSearchesWait:
    def test_documents_search_is_refused_while_a_question_is_pending(self, marqo_stub):
        ctx = _ctx([_user()] + _advisory_result(QUESTION))
        with pytest.raises(ModelRetry, match="Ask that question now"):
            asyncio.run(search_tools.search_documents(ctx, "aphids in wheat"))

    def test_pests_search_is_refused_while_a_question_is_pending(self, marqo_stub):
        ctx = _ctx([_user()] + _advisory_result(QUESTION))
        with pytest.raises(ModelRetry, match="Ask that question now"):
            asyncio.run(search_tools.search_pests_diseases(ctx, "aphids", "Wheat"))

    def test_both_run_after_advice_was_returned(self, marqo_stub):
        ctx = _ctx([_user()] + _advisory_result(ADVICE))
        assert asyncio.run(search_tools.search_documents(ctx, "aphids in wheat")).startswith("No ")
        assert asyncio.run(search_tools.search_pests_diseases(ctx, "aphids", "Wheat")).startswith("No ")

    def test_both_run_in_the_turn_after_the_farmer_answered(self, marqo_stub):
        history = [_user()] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user("Rabi, 40 days"),
        ]
        ctx = _ctx(history)
        assert asyncio.run(search_tools.search_documents(ctx, "aphids in wheat")).startswith("No ")


class TestAdvisoryResultsCarryTheMarker:
    def test_the_season_question_and_the_offer_carry_it_but_advice_does_not(self):
        assert PENDING_MARKER in sa.format_details_question(["Rabi"], True, False, "q")
        assert PENDING_MARKER in sa.format_season_suggestion("Zaid", "Wheat", ["Rabi"])
        assert PENDING_MARKER not in sa.format_advisory_results([{"title": "T", "text": "x", "section": "full"}], "q")
