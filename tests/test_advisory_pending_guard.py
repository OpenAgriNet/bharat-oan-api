"""search_advisory's follow-up questions to the farmer, and the guards on what only the farmer can say."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from agents.tools import advisory as sa
from helpers.advisory_pending import (
    CROP_MARKER,
    LOCATION_MARKER,
    PENDING_MARKER,
    SEASON_MARKER,
    farmer_gave_days,
    farmer_gave_season,
)

QUESTION = f"{PENDING_MARKER}{SEASON_MARKER}\n> Follow-up: ... end your answer with ONE short question"
ADVICE = "> Advisory Search Results for `q`\n\n**T**\n```\nAdvisory text\n```"


def _user(text="wheat aphids"):
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _advisory_result(content):
    return [
        ModelResponse(parts=[ToolCallPart(tool_name="search_advisory", args={})]),
        ModelRequest(parts=[ToolReturnPart(tool_name="search_advisory", tool_call_id="c1", content=content)]),
    ]


def _ctx(messages):
    deps = SimpleNamespace(session_id="s", question_id="q", skills=SimpleNamespace(allowed_tools=set()))
    return SimpleNamespace(deps=deps, messages=messages)


class TestAdvisoryResultsCarryTheMarker:
    def test_each_follow_up_carries_it_and_names_what_it_asks_but_advice_does_not(self):
        assert SEASON_MARKER in sa.format_details_followup(["Rabi"], True, False)
        assert SEASON_MARKER in sa.format_season_followup("Zaid", "Wheat", ["Rabi"])
        assert CROP_MARKER in sa.format_crop_followup(["Wheat", "Mustard"])
        assert LOCATION_MARKER in sa.format_details_followup([], False, False, True)
        combined = sa.format_details_followup(["Rabi"], True, True, True)
        assert SEASON_MARKER in combined and LOCATION_MARKER in combined
        for followup in (
            sa.format_details_followup(["Rabi"], True, False), sa.format_season_followup("Zaid", "Wheat", ["Rabi"]),
            sa.format_crop_followup(["Wheat", "Mustard"]), combined,
        ):
            assert followup.startswith(PENDING_MARKER) and "After the Source line" in followup
            assert "do not ask it again" in followup
        assert PENDING_MARKER not in sa.format_advisory_results([{"title": "T", "text": "x", "section": "full"}], "q")


class TestHardGuardsOnWhatOnlyTheFarmerCanSay:
    def _asked_before(self, reply):
        return [_user("wheat aphids")] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user(reply),
        ]

    def test_no_history_means_the_guards_stay_out_of_the_way(self):
        ctx = _ctx(None)
        assert farmer_gave_season(ctx) and farmer_gave_days(ctx)

    def test_a_season_the_farmer_never_wrote_is_not_accepted(self):
        ctx = _ctx([_user("How can I control aphids in my wheat crop?")])
        assert not farmer_gave_season(ctx)
        assert not farmer_gave_days(ctx)

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
        assert not farmer_gave_season(ctx) and not farmer_gave_days(ctx)

    def test_after_the_farmer_was_asked_whatever_they_answer_is_accepted(self):
        ctx = _ctx(self._asked_before("the winter one, since last month"))
        assert farmer_gave_season(ctx) and farmer_gave_days(ctx)


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
        assert SEASON_MARKER in out and "body" in out  # general advice, then the season follow-up
        assert seen["calls"][0] == {"days": None, "season": None}

    def test_the_farmers_own_details_are_used(self, monkeypatch):
        ctx = _ctx([_user("Wheat aphids, Rabi, 40 days after sowing")])
        out, seen = self._run(monkeypatch, ctx, season="Rabi", days_after_sowing=40)
        assert seen["calls"][0] == {"days": 40, "season": "Rabi"}
        assert PENDING_MARKER not in out

    def test_the_follow_up_is_not_asked_again_when_the_farmer_does_not_answer_it(self, monkeypatch):
        history = [_user("wheat aphids")] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Advice.\n\n**Source: ICAR - Kisan Sarathi**\n\nWhich season?")]),
            _user("I don't know"),
        ]
        out, _ = self._run(monkeypatch, _ctx(history))
        assert PENDING_MARKER not in out and "Search notes" in out


class TestPlaceGuards:
    from helpers.advisory_pending import LOCATION_MARKER  # noqa: E402

    def _location_asked(self, reply):
        return [_user("wheat aphids, Rabi")] + _advisory_result(f"{PENDING_MARKER}{self.LOCATION_MARKER} q") + [
            ModelResponse(parts=[TextPart(content="Which state and district?")]),
            _user(reply),
        ]

    def test_no_history_means_the_place_guards_stay_out_of_the_way(self):
        from helpers.advisory_pending import farmer_gave_place, farmer_wrote

        ctx = _ctx(None)
        assert farmer_gave_place(ctx) and farmer_wrote(ctx, "Agra")

    def test_a_place_in_the_farmers_message_counts(self):
        from helpers.advisory_pending import farmer_gave_place

        assert farmer_gave_place(_ctx([_user("wheat aphids in Agra")]))
        assert not farmer_gave_place(_ctx([_user("wheat aphids")]))

    def test_up_inside_ordinary_words_is_not_a_state(self):
        from helpers.advisory_pending import farmer_gave_place

        assert not farmer_gave_place(_ctx([_user("my wheat is growing up well")]))

    def test_the_answer_to_the_place_question_counts_but_only_what_it_literally_says(self):
        from helpers.advisory_pending import asked_location_in_previous_turn, farmer_gave_place, farmer_wrote

        ctx = _ctx(self._location_asked("our village near the river"))
        assert asked_location_in_previous_turn(ctx)
        assert farmer_gave_place(ctx)
        assert farmer_wrote(ctx, "village near the river")
        assert not farmer_wrote(ctx, "Uttar Pradesh")  # a place the model carried over is not the farmer's

    def test_a_place_given_earlier_in_the_same_question_counts_in_later_turns(self):
        from helpers.advisory_pending import answer_chain, farmer_gave_place, farmer_wrote

        history = (
            [_user("seed treatment")]
            + _advisory_result(QUESTION)
            + [ModelResponse(parts=[TextPart(content="q")]), _user("Yadgir, Karnataka")]
            + _advisory_result(QUESTION)
            + [ModelResponse(parts=[TextPart(content="q")]), _user("Zaid, 60 days")]
        )
        ctx = _ctx(history)
        assert [text for text, _ in answer_chain(ctx)] == ["seed treatment", "Yadgir, Karnataka", "Zaid, 60 days"]
        assert farmer_gave_place(ctx) and farmer_wrote(ctx, "Yadgir")

    def test_a_finished_question_ends_the_chain(self):
        from helpers.advisory_pending import answer_chain, farmer_gave_place

        history = (
            [_user("seed treatment in Agra")]
            + _advisory_result(ADVICE)
            + [ModelResponse(parts=[TextPart(content="advice")]), _user("harvest time?")]
        )
        ctx = _ctx(history)
        assert [text for text, _ in answer_chain(ctx)] == ["harvest time?"]
        assert not farmer_gave_place(ctx)

    def test_a_season_question_does_not_open_the_place_guard(self):
        from helpers.advisory_pending import asked_location_in_previous_turn, farmer_gave_place

        history = [_user("wheat aphids")] + _advisory_result(QUESTION) + [
            ModelResponse(parts=[TextPart(content="Which season?")]),
            _user("rabi, 40 days"),
        ]
        ctx = _ctx(history)
        assert not asked_location_in_previous_turn(ctx)
        assert not farmer_gave_place(ctx)

    def test_text_the_farmer_wrote_is_found_case_insensitively(self):
        from helpers.advisory_pending import farmer_wrote

        assert farmer_wrote(_ctx([_user("wheat aphids in PUNE")]), "Pune")
        assert not farmer_wrote(_ctx([_user("wheat aphids")]), "Pune")
