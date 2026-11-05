"""Tests for the search_advisory tool (BAP knowledge-advisory search)."""

import asyncio
import inspect
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from agents.tools import advisory as sa

# The provider appends the Source line to the stored chunk text.
TEXT = "Advisory: Harvest AKJ-1.\n\nSource: ICAR–DKMA, jak"

def _ctx(messages):
    return SimpleNamespace(deps=SimpleNamespace(session_id="sess-1", question_id="q-1"), messages=messages)


# the farmer already got an assistant reply earlier in the conversation (the generic or personalised question)
ASKED = [
    ModelRequest(parts=[UserPromptPart(content="How do I control aphids in my wheat crop?")]),
    ModelResponse(parts=[TextPart(content="Do you want generic or personalised advice?")]),
    ModelRequest(parts=[UserPromptPart(content='**User:** "personalised, Lucknow, Pune, 40 days"\n**Selected Language:** English')]),
]
# first turn: only the farmer's question, nothing said by the assistant yet
FIRST_TURN = [ModelRequest(parts=[UserPromptPart(content="How do I control aphids in my wheat crop?")])]

CTX = _ctx(ASKED)

def _tag(code, value):
    return {"descriptor": {"code": code, "name": code}, "value": value, "display": True}


def _item(chunk_id, title="Sorghum- Harvesting Management", text=TEXT, score="0.8321",
          source="ICAR–DKMA, jak", section="full", crop="Sorghum", doc_id="row-1"):
    return {
        "id": chunk_id,
        "descriptor": {"name": title, "code": section, "long_desc": text},
        "tags": [
            {
                "descriptor": {"code": "chunk-details", "name": "Chunk Details"},
                "list": [
                    _tag("title", title), _tag("crop", crop), _tag("section", section),
                    _tag("score", score), _tag("doc_id", doc_id), _tag("chunk_id", chunk_id),
                    _tag("source", source), _tag("text", text),
                ],
            }
        ],
    }


def _on_search(items, status="success", message="", query="aphids",
               location_status="not_requested", has_day_range="true", days_matched="false"):
    return {
        "context": {"action": "on_search"},
        "responses": [
            {
                "context": {"action": "on_search"},
                "message": {
                    "catalog": {
                        "descriptor": {"code": "knowledge-advisory"},
                        "tags": [
                            {
                                "descriptor": {"code": "search-context", "name": "Search Context"},
                                "list": [
                                    _tag("query", query), _tag("status", status),
                                    _tag("message", message), _tag("hit-count", str(len(items))),
                                    _tag("search-backend", "qdrant"),
                                    _tag("location-status", location_status),
                                    _tag("has-day-range", has_day_range),
                                    _tag("days-matched", days_matched),
                                ],
                            }
                        ],
                        "providers": [{"id": "advisory-documents", "items": items}] if items else [],
                    }
                },
            }
        ],
    }


@pytest.fixture
def bap(monkeypatch):
    """Point the tool at a fake BAP; returns a recorder of the requests it received."""
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap.test/")
    monkeypatch.setenv("BAP_ID", "bap-id")
    monkeypatch.setenv("BAP_URI", "https://bap.test")
    seen = SimpleNamespace(requests=[], response=httpx.Response(200, json=_on_search([_item("c1")])))
    real = httpx.AsyncClient

    def handler(request):
        seen.requests.append(request)
        return seen.response

    monkeypatch.setattr(sa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return seen


class TestPayload:
    def test_payload_targets_knowledge_advisory(self, monkeypatch):
        monkeypatch.setenv("BAP_ID", "bap-id")
        payload = sa.build_advisory_search_payload("aphids in wheat", session_id="s", question_id="q")
        category = payload["message"]["intent"]["category"]["descriptor"]
        assert category == {"code": "knowledge-advisory", "name": "knowledge-advisory"}
        assert payload["message"]["intent"]["item"]["descriptor"] == {"name": "aphids in wheat"}
        ctx = payload["context"]
        assert ctx["domain"] == "schemes:vistaar"
        assert ctx["action"] == "search"
        assert ctx["bap_id"] == "bap-id"
        assert ctx["tags"] == {"session_id": "s", "question_id": "q"}
        assert ctx["transaction_id"] and ctx["message_id"]


def _params(payload):
    """The search_params entries of a payload as {code: value}."""
    tags = payload["message"]["intent"]["item"].get("tags", [])
    group = next((t for t in tags if t["descriptor"]["code"] == "search_params"), None)
    if group is None:
        return None
    return {e["descriptor"]["code"]: e["value"] for e in group["list"]}


class TestSearchParams:
    def test_crop_season_and_days_travel_in_search_params(self):
        payload = sa.build_advisory_search_payload("aphids", crop="Wheat", days_after_sowing=45, season="Rabi")
        assert _params(payload) == {"crop": "Wheat", "days_after_sowing": "45", "season": "Rabi"}

    def test_a_location_is_never_sent(self):
        assert "district" not in inspect.signature(sa.build_advisory_search_payload).parameters

    def test_generic_search_sends_only_the_crop(self):
        assert _params(sa.build_advisory_search_payload("aphids", crop="Wheat")) == {"crop": "Wheat"}

    def test_no_tags_when_nothing_is_given(self):
        assert "tags" not in sa.build_advisory_search_payload("aphids")["message"]["intent"]["item"]

    def test_values_are_cleaned_and_bad_days_are_dropped(self):
        payload = sa.build_advisory_search_payload("aphids", crop="  Wheat\n\x00 ", days_after_sowing=-5)
        params = _params(payload)
        assert params["crop"] == "Wheat"
        assert "days_after_sowing" not in params
        assert "days_after_sowing" not in _params(sa.build_advisory_search_payload("a", crop="W", days_after_sowing=9999))

    def test_zero_days_is_kept(self):
        assert _params(sa.build_advisory_search_payload("a", crop="Wheat", days_after_sowing=0))["days_after_sowing"] == "0"


class TestParse:
    def test_parses_chunk_details(self):
        results, ctx = sa.parse_advisory_response(_on_search([_item("c1"), _item("c2", score="0.5")]))
        assert [r["chunk_id"] for r in results] == ["c1", "c2"]
        first = results[0]
        assert first["title"] == "Sorghum- Harvesting Management"
        assert first["crop"] == "Sorghum"
        assert first["section"] == "full"
        assert first["score"] == pytest.approx(0.8321)
        assert first["doc_id"] == "row-1"
        assert first["source"] == "ICAR–DKMA, jak"
        assert first["text"] == TEXT
        assert ctx["status"] == "success"
        assert ctx["hit_count"] == "2"

    def test_parses_location_and_day_range_notes(self):
        _, ctx = sa.parse_advisory_response(
            _on_search([_item("c1")], location_status="location_not_applied", has_day_range="true", days_matched="true")
        )
        assert ctx["location_status"] == "location_not_applied"
        assert ctx["has_day_range"] == "true"
        assert ctx["days_matched"] == "true"

    def test_empty_and_malformed_responses(self):
        assert sa.parse_advisory_response({}) == ([], {})
        assert sa.parse_advisory_response({"responses": []}) == ([], {})
        assert sa.parse_advisory_response({"responses": [None, "x", {"message": {}}]}) == ([], {})

    def test_non_success_status_with_no_items_returns_the_context(self):
        results, ctx = sa.parse_advisory_response(_on_search([], status="not_found", message="No advisory found"))
        assert results == []
        assert ctx["status"] == "not_found" and ctx["message"] == "No advisory found"

    def test_items_without_text_are_dropped(self):
        results, _ = sa.parse_advisory_response(_on_search([_item("c1", text="")]))
        assert results == []


class TestFormat:
    def test_formats_title_and_text(self):
        out = sa.format_advisory_results(
            [{"title": "Wheat Insect Management", "text": "Advisory: Inspect.\n\nSource: ICAR–DKMA", "section": "full"}],
            "aphids",
        )
        assert "aphids" in out
        assert "**Wheat Insect Management**" in out
        # the provider's "Source: institute, creator" line is removed, the fixed source is offered instead
        assert "ICAR–DKMA" not in out
        assert "Source: ICAR - Kisan Sarathi" in out


class TestSourceLine:
    def test_strips_the_providers_source_line_and_the_creator_name(self):
        out = sa.format_advisory_results(
            [{"title": "T", "text": "Advisory: Harvest.\n\nSource: ICAR–DKMA, jak", "section": "full"}], "q"
        )
        assert "jak" not in out and "DKMA" not in out
        assert out.rstrip().endswith("**Source: ICAR - Kisan Sarathi**")

    def test_leaves_text_without_a_source_line_alone(self):
        out = sa.format_advisory_results([{"title": "T", "text": "Advisory: Harvest.", "section": "full"}], "q")
        assert "Advisory: Harvest." in out

    def test_only_a_trailing_source_line_is_removed(self):
        text = "Source of water: canal.\nAdvisory: irrigate.\n\nSource: ICAR–DKMA, jak"
        out = sa.format_advisory_results([{"title": "T", "text": text, "section": "full"}], "q")
        assert "Source of water: canal." in out and "jak" not in out


class TestSearchNotes:
    def test_reports_day_range(self):
        ctx = {"has_day_range": "true", "days_matched": "false"}
        out = sa.format_search_notes(ctx, days_after_sowing=45)
        assert "advisory_has_day_range=yes" in out
        assert "days_after_sowing_matched=no" in out
        assert "location_status" not in out

    def test_tells_the_agent_when_days_did_not_match(self):
        ctx = {"has_day_range": "true", "days_matched": "false"}
        out = sa.format_search_notes(ctx, days_after_sowing=300)
        assert "no guidance for that sowing stage" in out and "Do not say the farmer did not give the days" in out

    def test_a_named_location_is_reported_as_not_used(self):
        ctx = {"has_day_range": "true", "days_matched": "true"}
        out = sa.format_search_notes(ctx, days_after_sowing=40, personalised=True, location="Lucknow")
        assert "Lucknow" in out and "no advisory specific to that location" in out
        assert "never used to search" in out

    def test_no_location_note_without_a_location(self):
        ctx = {"has_day_range": "true", "days_matched": "true"}
        assert "location" not in sa.format_search_notes(ctx, days_after_sowing=40, personalised=True)

    def test_says_so_when_a_personalised_farmer_skipped_the_days(self):
        ctx = {"has_day_range": "true", "days_matched": "false"}
        out = sa.format_search_notes(ctx, days_after_sowing=None, personalised=True)
        assert "did not give the days since sowing" in out

    def test_a_generic_search_never_mentions_skipped_details(self):
        ctx = {"has_day_range": "true", "days_matched": "false"}
        out = sa.format_search_notes(ctx, days_after_sowing=None, personalised=False)
        assert "did not give" not in out

    def test_days_matched_is_only_reported_when_days_were_sent(self):
        out = sa.format_search_notes({"has_day_range": "false"}, days_after_sowing=None)
        assert "advisory_has_day_range=no" in out
        assert "days_after_sowing_matched" not in out


class TestTool:
    def test_posts_to_bap_search_and_returns_formatted_advice(self, bap):
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", "Wheat", "generic"))
        request = bap.requests[0]
        assert str(request.url) == "http://bap.test/search"
        body = request.read().decode()
        assert '"knowledge-advisory"' in body and "aphids in wheat" in body
        assert "Harvest AKJ-1" in out and "ICAR" in out

    def test_sends_crop_season_and_days_and_reports_the_notes(self, bap):
        bap.response = httpx.Response(200, json=_on_search([_item("c1")], has_day_range="true", days_matched="true"))
        out = asyncio.run(
            sa.search_advisory(CTX, "aphids in wheat", "wheat", "personalised", season="Rabi", days_after_sowing=40)
        )
        body = json.loads(bap.requests[0].read())
        assert _params(body) == {"crop": "wheat", "days_after_sowing": "40", "season": "Rabi"}
        assert "Harvest AKJ-1" in out
        assert "days_after_sowing_matched=yes" in out
        assert "location" not in out

    def test_generic_call_sends_only_the_crop(self, bap):
        asyncio.run(sa.search_advisory(CTX, "aphids in wheat", "Wheat", "generic"))
        assert _params(json.loads(bap.requests[0].read())) == {"crop": "Wheat"}

    def test_blank_crop_asks_the_model_to_ask_the_farmer(self, bap):
        for blank in ("", "   "):
            with pytest.raises(ModelRetry):
                asyncio.run(sa.search_advisory(CTX, "aphids", blank, "generic"))
        assert bap.requests == []

    def test_a_named_location_is_not_sent_but_the_answer_says_so(self, bap):
        out = asyncio.run(
            sa.search_advisory(CTX, "aphids", "Wheat", "personalised", season="Rabi", days_after_sowing=40, location="Lucknow")
        )
        assert _params(json.loads(bap.requests[0].read())) == {"crop": "Wheat", "days_after_sowing": "40", "season": "Rabi"}
        assert "Lucknow" not in bap.requests[0].read().decode()
        assert "Harvest AKJ-1" in out  # advice is still given, not held back for a yes/no question
        assert "Lucknow" in out and "no advisory specific to that location" in out

    def test_generic_search_with_a_location_notes_it_too(self, bap):
        out = asyncio.run(sa.search_advisory(CTX, "aphids", "Wheat", "generic", location="Pune"))
        assert _params(json.loads(bap.requests[0].read())) == {"crop": "Wheat"}
        assert "Pune" in out and "no advisory specific to that location" in out

    def test_the_tool_has_no_district_parameter(self):
        params = inspect.signature(sa.search_advisory).parameters
        assert "district" not in params and "district_fallback_confirmed" not in params

    def test_generic_never_sends_days(self, bap):
        asyncio.run(sa.search_advisory(CTX, "aphids", "Wheat", "generic", days_after_sowing=40))
        assert _params(json.loads(bap.requests[0].read())) == {"crop": "Wheat"}

    def test_refuses_to_search_before_the_farmer_was_asked(self, bap):
        with pytest.raises(ModelRetry, match="generic or personalised"):
            asyncio.run(sa.search_advisory(_ctx(FIRST_TURN), "aphids", "Wheat", "generic"))
        assert bap.requests == []

    def test_refuses_on_an_empty_conversation(self, bap):
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx([]), "aphids", "Wheat", "personalised"))
        assert bap.requests == []

    def test_a_tool_only_reply_does_not_count_as_asking(self, bap):
        from pydantic_ai.messages import ToolCallPart

        only_tool_call = FIRST_TURN + [ModelResponse(parts=[ToolCallPart(tool_name="search_advisory", args={})])]
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx(only_tool_call), "aphids", "Wheat", "generic"))

    def test_allows_the_search_once_the_assistant_has_replied(self, bap):
        out = asyncio.run(sa.search_advisory(_ctx(ASKED), "aphids", "Wheat", "generic"))
        assert "Harvest AKJ-1" in out
        assert len(bap.requests) == 1

    def test_a_new_question_after_an_answer_needs_a_new_ask(self, bap):
        done = ASKED + [
            ModelRequest(parts=[ToolReturnPart(tool_name="search_advisory", tool_call_id="c1", content="advice")]),
            ModelResponse(parts=[TextPart(content="Here is the advice. Anything else?")]),
            ModelRequest(parts=[UserPromptPart(content="anything which you have")]),
        ]
        with pytest.raises(ModelRetry, match="generic or personalised"):
            asyncio.run(sa.search_advisory(_ctx(done), "wheat pests", "Wheat", "personalised"))
        asked_again = done + [
            ModelResponse(parts=[TextPart(content="Generic or personalised?")]),
            ModelRequest(parts=[UserPromptPart(content="generic")]),
        ]
        out = asyncio.run(sa.search_advisory(_ctx(asked_again), "wheat pests", "Wheat", "generic"))
        assert "Harvest AKJ-1" in out

    def test_a_question_answered_by_another_tool_does_not_count_as_asked(self, bap):
        # q1 answered through another search tool and "generic or personalised?" asked afterwards;
        # the next question must not be read as the farmer's reply to that late question.
        history = [
            ModelRequest(parts=[UserPromptPart(content="How can I control aphids in my wheat crop?")]),
            ModelResponse(parts=[TextPart(content="Let me look."), ToolCallPart(tool_name="search_documents", args={})]),
            ModelRequest(parts=[ToolReturnPart(tool_name="search_documents", tool_call_id="c9", content="aphid advice")]),
            ModelResponse(parts=[TextPart(content="Advice... Would you like generic or personalised advice?")]),
            ModelRequest(parts=[UserPromptPart(content="When is the right time to harvest my wheat crop?")]),
            ModelResponse(parts=[TextPart(content="Let me search."), ToolCallPart(tool_name="search_advisory", args={})]),
        ]
        with pytest.raises(ModelRetry, match="generic or personalised"):
            asyncio.run(sa.search_advisory(_ctx(history), "wheat harvest", "Wheat", "generic"))
        assert bap.requests == []

    def test_top_k_limits_results(self, bap):
        bap.response = httpx.Response(200, json=_on_search([_item(f"c{i}", title=f"T{i}") for i in range(6)]))
        out = asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "generic", top_k=2))
        assert "T0" in out and "T1" in out and "T2" not in out

    def test_empty_responses_say_nothing_was_found(self, bap):
        bap.response = httpx.Response(200, json={"context": {}, "responses": []})
        out = asyncio.run(sa.search_advisory(CTX, "something odd", "Wheat", "generic"))
        assert "No advisory" in out and "something odd" in out

    def test_provider_message_is_used_when_not_successful(self, bap):
        bap.response = httpx.Response(200, json=_on_search([], status="error", message="Advisory search failed"))
        assert asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "generic")) == "Advisory search failed"

    def test_non_200_means_unavailable(self, bap):
        bap.response = httpx.Response(502, text="bad gateway")
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "generic")).lower()

    def test_missing_bap_endpoint_is_a_configuration_message(self, monkeypatch):
        monkeypatch.delenv("BAP_ENDPOINT", raising=False)
        assert "not configured" in asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "generic")).lower()

    def test_network_error_means_unavailable(self, monkeypatch):
        monkeypatch.setenv("BAP_ENDPOINT", "http://bap.test")
        real = httpx.AsyncClient

        def boom(request):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(sa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom)))
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "generic")).lower()


def test_tool_is_registered_with_context():
    from agents.tools import TOOLS

    registered = {tool.name: tool for tool in TOOLS}
    assert "search_advisory" in registered
    assert registered["search_advisory"].takes_ctx is True
    schema = registered["search_advisory"].function_schema.json_schema
    assert "advice_type" in schema["required"]
    assert schema["properties"]["advice_type"]["enum"] == ["generic", "personalised"]
    assert "search_schemes" in registered  # scheme routing is untouched


def test_season_note_uses_env_default(monkeypatch):
    from agents.tools.advisory import default_season
    from agents.skill_catalog import get_catalog

    monkeypatch.setenv("ADVISORY_DEFAULT_SEASON", "Rabi")
    note = get_catalog().render_skill('crop.advice', 'en', {'advisory_default_season': default_season()})
    assert "Rabi" in note and "ask the farmer for the **season**" in note and "do not use their answer" in note

    monkeypatch.setenv("ADVISORY_DEFAULT_SEASON", "")
    note = get_catalog().render_skill('crop.advice', 'en', {'advisory_default_season': default_season()})
    assert "mandatory" in note and "pass their answer as `season`" in note


def test_payload_carries_season_tag():
    from agents.tools.advisory import build_advisory_search_payload

    payload = build_advisory_search_payload("wheat aphids", crop="Wheat", season="Rabi")
    entries = payload["message"]["intent"]["item"]["tags"][0]["list"]
    assert {"descriptor": {"code": "season"}, "value": "Rabi"} in entries


def test_env_season_beats_the_farmers_answer(monkeypatch, bap):
    monkeypatch.setenv("ADVISORY_DEFAULT_SEASON", "Rabi")
    asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "personalised", days_after_sowing=40, season="Kharif"))
    assert _params(json.loads(bap.requests[0].read()))["season"] == "Rabi"

    monkeypatch.setenv("ADVISORY_DEFAULT_SEASON", "")
    asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "personalised", days_after_sowing=40, season="Kharif"))
    assert _params(json.loads(bap.requests[1].read()))["season"] == "Kharif"


@pytest.fixture(autouse=True)
def _no_default_season(monkeypatch):
    monkeypatch.delenv("ADVISORY_DEFAULT_SEASON", raising=False)


def test_personalised_requires_a_valid_season(bap):
    for bad in (None, "", "monsoon"):
        with pytest.raises(ModelRetry, match="season is required"):
            asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "personalised", days_after_sowing=40, season=bad))
    assert bap.requests == []


def test_configured_season_means_the_farmer_need_not_give_one(monkeypatch, bap):
    monkeypatch.setenv("ADVISORY_DEFAULT_SEASON", "Rabi")
    out = asyncio.run(sa.search_advisory(CTX, "q", "Wheat", "personalised", days_after_sowing=40))
    assert "Harvest AKJ-1" in out
    assert _params(json.loads(bap.requests[0].read()))["season"] == "Rabi"


class TestDetailsAskedFirst:
    def test_personalised_without_days_needs_the_details_question(self, bap):
        # the farmer only chose "personalised"; the details question has not been asked yet
        with pytest.raises(ModelRetry, match="season"):
            asyncio.run(sa.search_advisory(CTX, "aphids", "Wheat", "personalised", season="Rabi"))
        assert bap.requests == []

    def test_the_details_question_asks_for_the_location_once(self):
        assert "location" in sa._DETAILS_RETRY and "never again" in sa._DETAILS_RETRY

    def test_no_location_means_no_location_note_and_no_second_question(self, bap):
        history = ASKED[:2] + [
            ModelRequest(parts=[UserPromptPart(content="personalised")]),
            ModelResponse(parts=[TextPart(content="Season, days since sowing and location?")]),
            ModelRequest(parts=[UserPromptPart(content="rabi, 40 days")]),
        ]
        out = asyncio.run(sa.search_advisory(_ctx(history), "harvest", "Wheat", "personalised", season="Rabi", days_after_sowing=40))
        assert "Harvest AKJ-1" in out and "location" not in out

    def test_proceeds_once_the_details_were_asked_and_answered(self, bap):
        history = ASKED[:2] + [
            ModelRequest(parts=[UserPromptPart(content="personalised")]),
            ModelResponse(parts=[TextPart(content="Which season and how many days since sowing?")]),
            ModelRequest(parts=[UserPromptPart(content="Rabi, I do not know the days")]),
        ]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids", "Wheat", "personalised", season="Rabi"))
        assert "Harvest AKJ-1" in out and "did not give the days" in out

    def test_details_in_the_call_need_no_second_question(self, bap):
        out = asyncio.run(sa.search_advisory(CTX, "aphids", "Wheat", "personalised", season="Rabi", days_after_sowing=40))
        assert "Harvest AKJ-1" in out

    def test_a_location_with_the_details_is_never_a_reason_to_ask_again(self, bap):
        # "rabi season, sowed 40 days ago and Lucknow": one answer is enough to search
        history = ASKED[:2] + [
            ModelRequest(parts=[UserPromptPart(content="personalised")]),
            ModelResponse(parts=[TextPart(content="Which season and how many days since sowing?")]),
            ModelRequest(parts=[UserPromptPart(content="rabi season, sowed 40 days ago and Lucknow state")]),
        ]
        out = asyncio.run(sa.search_advisory(_ctx(history), "harvest", "Wheat", "personalised", season="Rabi", days_after_sowing=40, location="Lucknow"))
        assert "Harvest AKJ-1" in out and "no advisory specific to that location" in out
