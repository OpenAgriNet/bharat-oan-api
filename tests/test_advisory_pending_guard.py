"""While search_advisory has a question for the farmer pending, other crop searches must wait."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from agents.tools import advisory as sa
from agents.tools import search as search_tools
from helpers.advisory_pending import (
    PENDING_MARKER,
    advisory_not_searched,
    advisory_question_pending,
    farmer_gave_days,
    farmer_gave_season,
    may_answer_now,
)

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


class TestPestsWaitForTheAdvisorySearch:
    def test_pests_search_is_refused_when_search_advisory_was_not_called_this_turn(self, marqo_stub):
        ctx = _ctx([_user()])
        with pytest.raises(ModelRetry, match="Call `search_advisory` first"):
            asyncio.run(search_tools.search_pests_diseases(ctx, "aphids", "Wheat"))

    def test_a_search_advisory_call_in_an_earlier_turn_does_not_count(self, marqo_stub):
        history = [_user()] + _advisory_result(ADVICE) + [
            ModelResponse(parts=[TextPart(content="Done")]),
            _user("and wheat rust?"),
        ]
        ctx = _ctx(history)
        assert advisory_not_searched(ctx)
        with pytest.raises(ModelRetry, match="Call `search_advisory` first"):
            asyncio.run(search_tools.search_pests_diseases(ctx, "rust", "Wheat"))

    def test_pests_search_runs_once_search_advisory_was_called_this_turn(self, marqo_stub):
        ctx = _ctx([_user()] + _advisory_result(ADVICE))
        assert not advisory_not_searched(ctx)
        assert asyncio.run(search_tools.search_pests_diseases(ctx, "aphids", "Wheat")).startswith("No ")

    def test_documents_search_does_not_need_the_advisory_search(self, marqo_stub):
        ctx = _ctx([_user("cattle fever")])
        assert asyncio.run(search_tools.search_documents(ctx, "cattle fever")).startswith("No ")


class TestHardGuardsOnWhatOnlyTheFarmerCanSay:
    def _asked_before(self, reply):
        return [_user("wheat aphids")] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user(reply),
        ]

    def test_no_history_means_the_guards_stay_out_of_the_way(self):
        ctx = _ctx(None)
        assert farmer_gave_season(ctx) and farmer_gave_days(ctx) and may_answer_now(ctx)

    def test_a_season_the_farmer_never_wrote_is_not_accepted(self):
        ctx = _ctx([_user("How can I control aphids in my wheat crop?")])
        assert not farmer_gave_season(ctx)
        assert not farmer_gave_days(ctx)
        assert not may_answer_now(ctx)

    @pytest.mark.parametrize("text", ["wheat aphids, Rabi", "kharif season", "गेहूं रबी में", "ખરીફ"])
    def test_a_season_word_in_the_farmers_text_is_accepted(self, text):
        assert farmer_gave_season(_ctx([_user(text)]))

    @pytest.mark.parametrize("text", ["sowed 40 days ago", "two months ago", "40 दिन"])
    def test_days_in_the_farmers_text_are_accepted(self, text):
        assert farmer_gave_days(_ctx([_user(text)]))

    def test_earlier_answers_in_the_conversation_text_do_not_count(self):
        prefix = "**Conversation**\n\nUser: rabi, 40 days\nAssistant: At 40 days in Rabi ...\n\n---\n\n"
        ctx = _ctx([_user(prefix + "When should I harvest an early-maturing wheat variety?")])
        assert not farmer_gave_season(ctx)
        assert not farmer_gave_days(ctx)
        assert not may_answer_now(ctx)

    def test_the_farmers_own_words_after_the_conversation_text_count(self):
        prefix = "**Conversation**\n\nUser: hi\n\n---\n\n"
        ctx = _ctx([_user(prefix + "Wheat aphids, Rabi, 40 days")])
        assert farmer_gave_season(ctx) and farmer_gave_days(ctx)

    def test_a_question_from_two_turns_back_does_not_open_the_guards(self):
        history = (
            [_user("wheat aphids")] + _advisory_result(QUESTION)
            + [ModelResponse(parts=[TextPart(content="Which season?")]), _user("rabi, 40 days")]
            + _advisory_result(ADVICE)
            + [ModelResponse(parts=[TextPart(content="Advice")]), _user("When to harvest wheat?")]
        )
        ctx = _ctx(history)
        assert not farmer_gave_season(ctx) and not farmer_gave_days(ctx) and not may_answer_now(ctx)

    def test_answer_now_is_allowed_when_tool_results_of_earlier_turns_were_not_kept(self):
        history = [
            _user("wheat aphids"),
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user("I don't know"),
        ]
        assert may_answer_now(_ctx(history))

    def test_after_the_farmer_was_asked_whatever_they_answer_is_accepted(self):
        ctx = _ctx(self._asked_before("the winter one, since last month"))
        assert farmer_gave_season(ctx) and farmer_gave_days(ctx) and may_answer_now(ctx)

    def test_answer_now_is_not_honoured_in_the_same_turn_as_the_question(self):
        ctx = _ctx([_user("wheat aphids")] + _advisory_result(QUESTION))
        assert not may_answer_now(ctx)


def _chunks():
    return [
        {"title": "T", "text": "Crop: Wheat | Problem type: Pest | Season: Rabi\nbody", "crop": "Wheat",
         "section": "full", "source": "s", "score": 1.0}
    ]


class TestToolIgnoresInventedDetails:
    def _run(self, monkeypatch, ctx, **kwargs):
        seen = {}

        async def fake_run_search(_ctx, query, days, season, **kw):
            seen.setdefault("calls", []).append({"days": days, "season": season})
            return _chunks(), {"has_day_range": "true", "days_matched": "false"}, 200, ""

        monkeypatch.setattr(sa, "_run_search", fake_run_search)
        out = asyncio.run(sa.search_advisory(ctx, "wheat aphids", crop="Wheat", **kwargs))
        return out, seen

    def test_an_invented_season_and_days_make_the_tool_ask(self, monkeypatch):
        ctx = _ctx([_user("How can I control aphids in my wheat crop?")])
        out, seen = self._run(monkeypatch, ctx, season="Rabi", days_after_sowing=40)
        assert PENDING_MARKER in out
        assert seen["calls"][0] == {"days": None, "season": None}

    def test_an_invented_answer_now_does_not_skip_the_question(self, monkeypatch):
        ctx = _ctx([_user("wheat aphids")] + _advisory_result(QUESTION))
        out, _ = self._run(monkeypatch, ctx, answer_now=True)
        assert PENDING_MARKER in out

    def test_the_farmers_own_details_are_used(self, monkeypatch):
        ctx = _ctx([_user("Wheat aphids, Rabi, 40 days after sowing")])
        out, seen = self._run(monkeypatch, ctx, season="Rabi", days_after_sowing=40)
        assert seen["calls"][0] == {"days": 40, "season": "Rabi"}
        assert PENDING_MARKER not in out

    def test_answer_now_works_after_the_farmer_was_asked_and_said_they_do_not_know(self, monkeypatch):
        history = [_user("wheat aphids")] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user("I don't know"),
        ]
        out, _ = self._run(monkeypatch, _ctx(history), answer_now=True)
        assert PENDING_MARKER not in out
