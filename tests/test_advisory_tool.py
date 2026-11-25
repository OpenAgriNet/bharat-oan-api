"""Tests for the search_advisory tool (Knowledge Provider /search, advisory profile)."""

import asyncio
import inspect
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry

from agents.tools import advisory as sa
from helpers import knowledge_provider

# The provider appends the Source line to the stored chunk text.
TEXT = "Advisory: Harvest AKJ-1.\n\nSource: ICAR–DKMA, jak"

def _ctx(messages):
    # No IMD agromet tool in these tests, so the agromet offer note stays empty.
    deps = SimpleNamespace(session_id="sess-1", question_id="q-1", skills=SimpleNamespace(allowed_tools=set()))
    return SimpleNamespace(deps=deps, messages=messages)


CTX = _ctx([])

def _item(chunk_id, title="Sorghum- Harvesting Management", text=TEXT, score="0.8321",
          source="ICAR–DKMA, jak", section="full", crop="Sorghum", doc_id="row-1", location=None, origin=None):
    """One KP advisory hit. `location` is an optional dict: match, national, states, districts.

    `origin` is KP's intake channel (docs-pipeline, webhook); it defaults to `source`.
    """
    hit = {
        "_id": chunk_id, "chunk_id": chunk_id, "title_en": title, "text": text, "section": section,
        "_advisory_score": float(score), "doc_id": doc_id, "origin": source if origin is None else origin,
        "crop_keys": [crop] if crop else [],
    }
    if location:
        hit["location_match"] = location.get("match", "not_requested")
        hit["advisory"] = {
            "location_national": location.get("national", False),
            "location_state_names": location.get("states", []),
            "location_district_names": location.get("districts", []),
        }
    return hit


def _on_search(items, status="success", message="", query="aphids",
               location_status="not_requested", has_day_range="true", days_matched="false"):
    """A KP /search response for advisory documents."""
    return {
        "effective_config": {"query": query, "document_kind": "advisory", "ranking_score_field": "_advisory_score"},
        "final_count": len(items),
        "hits": items,
        "advisory_search": {
            "status": status, "message": message or None, "location_status": location_status,
            "has_day_range": has_day_range == "true", "days_matched": days_matched == "true",
        },
    }


@pytest.fixture
def kp(monkeypatch):
    """Point the tool at a fake KP (token endpoint + /search); records the search requests it received."""
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_URL", "http://kp.test/kp-api/")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_TOKEN_URL", "http://kp.test/token")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_CLIENT_ID", "bharat-oan-api")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(knowledge_provider, "_token", None)
    seen = SimpleNamespace(requests=[], queue=[], response=httpx.Response(200, json=_on_search([_item("c1")])))
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        seen.requests.append(request)
        return seen.queue.pop(0) if seen.queue else seen.response

    monkeypatch.setattr(sa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return seen


class TestPayload:
    def test_payload_uses_the_advisory_profile(self):
        payload = sa.build_advisory_search_payload("aphids in wheat")
        assert payload == {"query": "aphids in wheat", "document_kind": "advisory"}


def _params(payload):
    """The farmer context and top_k of a KP advisory body as {name: str(value)}; None when neither is sent."""
    params = {key: str(value) for key, value in (payload.get("advisory") or {}).items()}
    if "top_k" in payload:
        params["top_k"] = str(payload["top_k"])
    return params or None


class TestSearchParams:
    def test_crop_season_and_days_travel_in_search_params(self):
        payload = sa.build_advisory_search_payload("aphids", crop="Wheat", days_after_sowing=45, season="Rabi")
        assert _params(payload) == {"crop": "Wheat", "days_after_sowing": "45", "season": "Rabi"}

    def test_the_farmer_place_travels_as_lgd_codes(self):
        payload = sa.build_advisory_search_payload("aphids", crop="Wheat", state_lgd_code=9, district_lgd_code=118)
        assert _params(payload) == {"crop": "Wheat", "state_lgd_code": "9", "district_lgd_code": "118"}

    def test_no_place_codes_are_sent_when_the_place_is_unknown(self):
        assert _params(sa.build_advisory_search_payload("aphids", crop="Wheat")) == {"crop": "Wheat"}

    def test_top_k_travels_clamped(self):
        assert _params(sa.build_advisory_search_payload("a", top_k=7)) == {"top_k": "7"}
        assert _params(sa.build_advisory_search_payload("a", top_k=500)) == {"top_k": "20"}
        assert _params(sa.build_advisory_search_payload("a", top_k=0)) is None

    def test_crop_only_search_sends_only_the_crop(self):
        assert _params(sa.build_advisory_search_payload("aphids", crop="Wheat")) == {"crop": "Wheat"}

    def test_no_tags_when_nothing_is_given(self):
        assert "advisory" not in sa.build_advisory_search_payload("aphids")

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

    def test_reads_where_each_chunk_came_from(self):
        results, _ = sa.parse_advisory_response(
            _on_search([_item("c1", origin="docs-pipeline"), _item("c2", origin="webhook"), _item("c3", origin="")])
        )
        assert [r["origin"] for r in results] == ["docs-pipeline", "webhook", ""]


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

    def test_cites_the_pdf_name_for_an_advisory_uploaded_as_a_pdf(self):
        out = sa.format_advisory_results(
            [{"title": "coffee_guide_2023_mini", "text": "Spray Bordeaux mixture.", "section": "full",
              "origin": "docs-pipeline"}],
            "q",
        )
        assert out.rstrip().endswith("**Source: coffee_guide_2023_mini**")
        assert "Kisan Sarathi" not in out

    def test_cites_kisan_sarathi_for_a_webhook_advisory(self):
        out = sa.format_advisory_results(
            [{"title": "T", "text": TEXT, "section": "full", "origin": "webhook"}], "q"
        )
        assert out.rstrip().endswith("**Source: ICAR - Kisan Sarathi**")

    def test_cites_both_when_pdf_and_kisan_sarathi_advisories_are_returned(self):
        out = sa.format_advisory_results(
            [
                {"title": "Wheat Insect Management", "text": TEXT, "section": "full", "origin": "webhook"},
                {"title": "coffee_guide_2023_mini", "text": "Spray.", "section": "full", "origin": "docs-pipeline"},
                {"title": "Package of Practices for Wheat", "text": TEXT, "section": "full", "origin": "webhook"},
            ],
            "q",
        )
        assert out.rstrip().endswith("**Source: ICAR - Kisan Sarathi; coffee_guide_2023_mini**")

    def test_leaves_text_without_a_source_line_alone(self):
        out = sa.format_advisory_results([{"title": "T", "text": "Advisory: Harvest.", "section": "full"}], "q")
        assert "Advisory: Harvest." in out

    def test_only_a_trailing_source_line_is_removed(self):
        text = "Source of water: canal.\nAdvisory: irrigate.\n\nSource: ICAR–DKMA, jak"
        out = sa.format_advisory_results([{"title": "T", "text": text, "section": "full"}], "q")
        assert "Source of water: canal." in out and "jak" not in out



def _chunk(chunk_id, season=None, crop="Wheat", title="Wheat Insect Management", **kw):
    """A chunk whose text has the docs-pipeline header line (`Crop: .. | Problem type: .. | Season: ..`)."""
    header = f"Crop: {crop} | Problem type: Insect"
    if season is not None:
        header += f" | Season: {season}"
    text = f"{title}\n{header}\nAdvisory: Scout for aphids ({chunk_id}).\n\nSource: ICAR–DKMA, jak"
    return _item(chunk_id, title=title, text=text, crop=crop, **kw)


def _respond(items, **kw):
    return httpx.Response(200, json=_on_search(items, **kw))


def _followup(out):
    """The follow-up part of a result ('' when the answer ends without one)."""
    return out.split("> Follow-up:", 1)[1] if "> Follow-up:" in out else ""


def _sent(kp, index):
    return json.loads(kp.requests[index].read())


class TestSeasonParsing:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("T\nCrop: Wheat | Problem type: Insect | Season: Rabi\nAdvisory: x", ["rabi"]),
            ("T\nCrop: Wheat | Season: Rabi,Kharif\nAdvisory: x", ["rabi", "kharif"]),
            ("T\nSeason: Rabi / Zaid\nAdvisory: x", ["rabi", "zaid"]),
            ("T\nCrop: Wheat | Season: OTHER\nAdvisory: x", ["other"]),
            ("T\nCrop: Wheat | Season: \nAdvisory: x", []),
            ("T\nCrop: Wheat\nAdvisory: x", []),
            ("", []),
        ],
    )
    def test_reads_the_season_header(self, text, expected):
        assert sa.parse_chunk_seasons(text) == expected

    def test_a_season_word_in_the_body_is_not_the_header(self):
        assert sa.parse_chunk_seasons("T\nCrop: Wheat\nAdvisory: sow in the Season: Rabi") == []

    def test_results_carry_their_seasons(self):
        results, _ = sa.parse_advisory_response(_on_search([_chunk("c1", "Rabi,Kharif")]))
        assert results[0]["seasons"] == ["rabi", "kharif"]

    def test_matching_rules(self):
        rabi = {"seasons": ["rabi"]}
        assert sa.season_matches(rabi, "Rabi") and not sa.season_matches(rabi, "Zaid")
        assert sa.season_matches({"seasons": ["rabi", "kharif"]}, "kharif")
        # Other / no season / unreadable header: not tied to a season, matches any
        assert sa.season_matches({"seasons": ["other"]}, "Zaid")
        assert sa.season_matches({"seasons": []}, "Zaid")

    def test_explicit_seasons_are_distinct_and_exclude_other(self):
        results = [{"seasons": ["rabi"]}, {"seasons": ["other"]}, {"seasons": ["kharif", "rabi"]}, {"seasons": []}]
        assert sa.explicit_seasons(results) == ["Rabi", "Kharif"]

    def test_normalize_season(self):
        assert sa.normalize_season(" rabi ") == "Rabi"
        assert sa.normalize_season("summer") == "Zaid"
        assert sa.normalize_season(None) == ""

    def test_crop_relevance(self):
        wheat, rice, nameless = {"crop": "Wheat"}, {"crop": "Rice"}, {"crop": ""}
        # crop named by the model: only that crop's chunks, and it is the re-query filter
        assert sa.relevant_for_crop([wheat, rice], "Rice", "aphids") == ([rice], "Rice")
        assert sa.relevant_for_crop([wheat, rice], "sorghum", "aphids") == ([], "sorghum")
        # no hint: a crop named in the query picks the chunks
        assert sa.relevant_for_crop([wheat, rice], "", "aphids in rice") == ([rice], "Rice")
        # nothing tells the crop: keep everything, no filter
        assert sa.relevant_for_crop([wheat, rice, nameless], "", "aphids") == ([wheat, rice, nameless], "")


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

    def test_a_named_place_that_cannot_be_matched_is_reported(self):
        ctx = {"has_day_range": "true", "days_matched": "true"}
        out = sa.format_search_notes(ctx, days_after_sowing=40, location="Lucknow")
        assert "I could not match Lucknow to a state or district" in out

    def test_no_location_note_without_a_location(self):
        ctx = {"has_day_range": "true", "days_matched": "true"}
        assert "location" not in sa.format_search_notes(ctx, days_after_sowing=40)

    def test_skipped_days_are_never_mentioned(self):
        ctx = {"has_day_range": "true", "days_matched": "false"}
        assert "did not give" not in sa.format_search_notes(ctx, days_after_sowing=None)

    def test_general_advice_note_without_a_day_range(self):
        out = sa.format_search_notes({"has_day_range": "false"}, days_after_sowing=None)
        assert "advisory_has_day_range=no" in out and "not tied to a sowing stage" in out
        assert "days_after_sowing_matched" not in out


# no day range in the chunks, so only the season decides whether the farmer is asked
NO_DAYS = {"has_day_range": "false"}


class TestSearchFirst:
    def test_posts_to_kp_search_and_returns_formatted_advice(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Sorghum"))
        request = kp.requests[0]
        assert str(request.url) == "http://kp.test/kp-api/search"
        assert request.headers["Authorization"] == "Bearer tok"
        body = json.loads(request.read())
        assert body["document_kind"] == "advisory" and body["query"] == "aphids in wheat"
        assert "Harvest AKJ-1" in out and "ICAR" in out

    def test_the_first_search_sends_no_crop_filter(self, kp):
        kp.response = _respond([_chunk("c1")], **NO_DAYS)
        asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat"))
        assert len(kp.requests) == 1
        assert _params(_sent(kp, 0)) == {"top_k": "5"}

    def test_details_in_the_first_message_are_sent_with_the_first_search(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], has_day_range="true", days_matched="true")
        out = asyncio.run(
            sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", days_after_sowing=40, season="rabi", location="Pune")
        )
        assert _params(_sent(kp, 0)) == {"days_after_sowing": "40", "season": "Rabi", "top_k": "5"}
        assert "Scout for aphids (c1)" in out and "days_after_sowing_matched=yes" in out
        assert "I could not match Pune to a state or district" in out
        assert len(kp.requests) == 1

    def test_the_tool_signature(self):
        params = inspect.signature(sa.search_advisory).parameters
        assert list(params)[1:] == [
            "query", "crop", "days_after_sowing", "season", "location", "top_k", "state", "district",
            "crop_words", "image_url",
        ]
        assert "advice_type" not in params

    def test_a_place_that_cannot_be_matched_is_mentioned_but_not_sent_or_asked(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids", crop="Sorghum", location="Lucknow"))
        assert "state_lgd_code" not in kp.requests[0].read().decode()
        assert "Harvest AKJ-1" in out and "I could not match Lucknow to a state or district" in out
        assert sa.PENDING_MARKER not in out

    def test_no_location_means_no_location_text(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        assert "location" not in asyncio.run(sa.search_advisory(CTX, "aphids", crop="Sorghum")).lower()

    def test_top_k_limits_results(self, kp):
        kp.response = _respond([_item(f"c{i}", title=f"T{i}") for i in range(6)], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "q", crop="Sorghum", top_k=2))
        assert "T0" in out and "T1" in out and "T2" not in out


class TestFollowUpForWhatTheChunksNeed:
    """Answer first: the advice is always given; a missing season / days becomes ONE follow-up after the Source line."""

    def test_a_named_season_in_the_chunks_means_a_season_follow_up_even_for_one_season(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi"), _chunk("c2", "Rabi")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat"))
        assert "Scout for aphids (c1)" in out and "Cite as" in out  # the advice comes first
        assert sa.SEASON_MARKER in out and out.index("Cite as") < out.index("> Follow-up:")
        followup = _followup(out)
        assert "Could you please tell me which season you are growing your crop in?" in followup
        assert "Rabi" not in followup  # one season: do not hint that it is the expected one
        assert "general advice" in followup and "do not say which season" in followup
        assert len(kp.requests) == 1

    def test_several_seasons_are_all_named_in_the_follow_up(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi"), _chunk("c2", "Kharif")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat"))
        followup = _followup(out)
        assert "cover the Rabi and Kharif seasons" in followup and "(Rabi" not in followup
        assert "farmer decides" in followup and "your only follow-up question" in followup
        assert "Scout for aphids" in out

    def test_season_and_days_are_asked_in_one_follow_up(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], has_day_range="true")
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat"))
        followup = _followup(out)
        assert "Could you please tell me which season you are growing your crop in, and how many days ago you sowed it?" in followup
        assert "`season` and `days_after_sowing`" in followup
        assert "Scout for aphids (c1)" in out

    def test_only_the_days_are_asked_when_the_season_is_known(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], has_day_range="true")
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Rabi"))
        followup = _followup(out)
        assert "Could you please tell me how many days ago you sowed your crop?" in followup and "which season" not in followup
        assert "Scout for aphids" in out

    def test_nothing_is_asked_when_the_chunks_have_no_named_season_or_day_range(self, kp):
        kp.response = _respond([_chunk("c1", "Other"), _chunk("c2")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat"))
        assert "Scout for aphids (c1)" in out and sa.PENDING_MARKER not in out

    def test_a_season_follow_up_is_not_asked_again_when_the_farmer_does_not_answer_it(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], has_day_range="true")
        history = [_user("aphids in my wheat")] + _asked(f"{sa.PENDING_MARKER}{sa.SEASON_MARKER} q") + [_user("I do not know")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat"))
        assert "Scout for aphids (c1)" in out and sa.PENDING_MARKER not in out

    def test_the_answer_to_the_season_follow_up_gives_the_specific_advice(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi"), _chunk("c2", "Kharif")], has_day_range="true", days_matched="true")
        history = [_user("aphids in my wheat")] + _asked(f"{sa.PENDING_MARKER}{sa.SEASON_MARKER} q") + [_user("Rabi, 40 days")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", days_after_sowing=40))
        assert "(c1)" in out and "(c2)" not in out and sa.PENDING_MARKER not in out
        assert _params(_sent(kp, 0))["season"] == "Rabi"

    def test_an_answer_without_the_place_is_used_as_given_and_nothing_more_is_asked(self, kp):
        kp.response = _respond([_located()], has_day_range="true", days_matched="true")
        asked = f"{sa.PENDING_MARKER}{sa.SEASON_MARKER}{sa.LOCATION_MARKER} q"
        history = [_user("aphids in my wheat")] + _asked(asked) + [_user("Rabi, 45 days")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", days_after_sowing=45))
        assert '"This advice is for your Rabi wheat crop at 45 days after sowing. It is not specific to your area."' in out
        assert sa.PENDING_MARKER not in out and "could not match" not in out
        assert "Already asked" in out and "Do not ask for their state and district again" in out

    def test_an_answer_with_everything_gives_the_place_and_stage_opening(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "matched"})], has_day_range="true", days_matched="true")
        asked = f"{sa.PENDING_MARKER}{sa.SEASON_MARKER}{sa.LOCATION_MARKER} q"
        history = [_user("aphids in my wheat")] + _asked(asked) + [_user("Rabi, 45 days, Agra, Uttar Pradesh")]
        out = asyncio.run(sa.search_advisory(
            _ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", days_after_sowing=45, state="Uttar Pradesh", district="Agra",
        ))
        assert ('"This advice is for your district, Agra, in Uttar Pradesh. It is for your Rabi wheat crop at 45 days '
                'after sowing."') in out
        assert sa.PENDING_MARKER not in out and "Already asked" not in out  # everything was given

    def test_a_place_the_lookup_does_not_know_is_named_without_the_season_and_days(self, kp):
        kp.response = _respond([_located()], has_day_range="true", days_matched="true")
        asked = f"{sa.PENDING_MARKER}{sa.SEASON_MARKER}{sa.LOCATION_MARKER} q"
        history = [_user("aphids in my wheat")] + _asked(asked) + [_user("Rabi, 45 days, Bangalore")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", days_after_sowing=45))
        assert "I could not match Bangalore to a state or district." in out and sa.PENDING_MARKER not in out
        assert "Do not ask for their state and district again" in out

    def test_everything_given_is_answered_in_one_search(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], has_day_range="true", days_matched="true")
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Rabi", days_after_sowing=40))
        assert "Scout for aphids (c1)" in out and len(kp.requests) == 1 and sa.PENDING_MARKER not in out


def _staged(chunk_id, start, end, season="Kharif", crop="Sorghum"):
    """An ICAR-style chunk with its sowing-day range lines."""
    text = (
        f"Problem: P{chunk_id}\nCommodity: {crop}\nSeason: {season}\nStart day relative to sowing: {start}\n"
        f"End day relative to sowing: {end}\nAdvisory: Do {chunk_id}.\n\nSource: ICAR–DKMA, jak"
    )
    return _item(chunk_id, title=f"T {chunk_id}", text=text, crop=crop)


class TestDayRange:
    @pytest.mark.parametrize("text, expected", [
        ("Start day relative to sowing: 10\nEnd day relative to sowing: 30", (10, 30)),
        ("Start day relative to sowing: -30\nEnd day relative to sowing: -7", (-30, -7)),
        ("Start day relative to sowing: 0\nEnd day relative to sowing: 0", None),
        ("Start day relative to sowing: 10", None),
        ("no range here", None),
    ])
    def test_reads_the_sowing_day_range(self, text, expected):
        assert sa.parse_chunk_days(text) == expected

    def test_keeps_advisories_for_the_farmers_stage_and_those_without_a_range(self):
        results = [{"id": "shoot fly", "day_range": (10, 30)}, {"id": "harrowing", "day_range": (-30, -7)},
                   {"id": "general", "day_range": None}]
        assert [r["id"] for r in sa.for_days(results, 15)] == ["shoot fly", "general"]
        assert sa.for_days(results, None) == results

    def test_keeps_everything_when_no_advisory_fits_the_stage(self):
        results = [{"id": "harrowing", "day_range": (-30, -7)}]
        assert sa.for_days(results, 200) == results

    def test_advice_for_another_sowing_stage_is_dropped(self, kp):
        kp.response = _respond(
            [_staged("shootfly", 10, 30), _staged("harrowing", -30, -7), _staged("heat", 20, 100)],
            has_day_range="true", days_matched="true",
        )
        history = [_user("sorghum care")] + _asked(f"{sa.PENDING_MARKER}{sa.SEASON_MARKER} q") + [_user("Kharif, 15 days")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "sorghum care", crop="Sorghum", season="Kharif", days_after_sowing=15))
        assert "Do shootfly" in out and "Do harrowing" not in out and "Do heat" not in out


class TestSeasonInspection:
    def test_season_in_the_chunks_is_one_search_and_keeps_only_matching(self, kp):
        kp.response = _respond([_chunk("c1", "Kharif"), _chunk("c2", "Rabi,Zaid"), _chunk("c3", "Other")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Rabi"))
        assert len(kp.requests) == 1
        assert "(c2)" in out and "(c3)" in out and "(c1)" not in out

    def test_a_season_missing_from_the_chunks_triggers_exactly_one_requery(self, kp):
        kp.queue = [
            _respond([_chunk("c1", "Rabi"), _chunk("c2", "Kharif")]),
            _respond([_chunk("c9", "Zaid")], has_day_range="true", days_matched="true"),
        ]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Zaid", days_after_sowing=30))
        assert len(kp.requests) == 2
        assert "(c9)" in out and "(c1)" not in out and "days_after_sowing_matched=yes" in out
        assert "crop" not in (_params(_sent(kp, 0)) or {})
        assert _params(_sent(kp, 1)) == {"crop": "Wheat", "days_after_sowing": "30", "season": "Zaid", "top_k": "20"}

    def test_requery_finding_nothing_gives_general_advice_and_offers_the_seasons_that_exist(self, kp):
        kp.queue = [
            _respond([_chunk("c1", "Rabi"), _chunk("c2", "Kharif")]),
            _respond([_chunk("c3", "Rabi")]),
        ]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", season="Zaid", crop="Wheat"))
        assert len(kp.requests) == 2  # never a third search
        followup = _followup(out)
        assert "No advisory is available for the Zaid season for Wheat" in followup and sa.SEASON_MARKER in out
        assert "Rabi" in followup and "Kharif" in followup
        assert "Scout for aphids" in out and "Cite as" in out  # general advice first, then the offer

    def test_requery_with_an_empty_response_still_suggests(self, kp):
        kp.queue = [_respond([_chunk("c1", "Rabi")]), httpx.Response(200, json={"responses": []})]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Kharif"))
        assert "No advisory is available for the Kharif season" in out and "Rabi" in out

    def test_requery_failure_still_suggests_instead_of_failing(self, kp):
        kp.queue = [_respond([_chunk("c1", "Rabi")]), httpx.Response(502, text="bad")]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Kharif"))
        assert "No advisory is available for the Kharif season" in out

    def test_a_first_search_with_no_results_is_not_requeried(self, kp):
        kp.response = httpx.Response(200, json={"context": {}, "responses": []})
        out = asyncio.run(sa.search_advisory(CTX, "something odd", crop="Sorghum", season="Rabi"))
        assert len(kp.requests) == 1
        assert "No advisory" in out and "something odd" in out


class TestCropCheck:
    def test_chunks_of_another_crop_trigger_one_requery_with_the_crop_filter(self, kp):
        kp.queue = [
            _respond([_chunk("w1", "Rabi", crop="Wheat"), _chunk("w2", "Rabi", crop="Wheat")]),
            _respond([_chunk("s1", "Kharif", crop="Sorghum")], **NO_DAYS),
        ]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in sorghum", crop="Sorghum"))
        assert len(kp.requests) == 2
        assert _params(_sent(kp, 1))["crop"] == "Sorghum"
        followup = _followup(out)
        assert "Could you please tell me which season you are growing your crop in?" in followup and "Kharif" not in followup
        assert "(s1)" in out and "w1" not in out

    def test_other_crop_chunks_are_never_shown_when_the_crop_has_no_advisory(self, kp):
        kp.queue = [
            _respond([_chunk("w1", "Rabi", crop="Wheat")]),
            httpx.Response(200, json={"responses": []}),
        ]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in sorghum", crop="Sorghum"))
        assert len(kp.requests) == 2
        assert "No advisory found" in out and "Scout for aphids" not in out

    def test_a_sorghum_chunk_among_wheat_chunks_is_enough_no_requery(self, kp):
        kp.response = _respond(
            [_chunk("w1", "Rabi", crop="Wheat"), _chunk("s1", "Kharif", crop="Sorghum")], **NO_DAYS
        )
        out = asyncio.run(sa.search_advisory(CTX, "aphids in sorghum", crop="sorghum"))
        assert len(kp.requests) == 1
        assert "(s1)" in out and "(w1)" not in out

    def test_one_requery_covers_both_a_crop_miss_and_a_season_miss(self, kp):
        kp.queue = [
            _respond([_chunk("w1", "Rabi", crop="Wheat")]),
            _respond([_chunk("s1", "Kharif", crop="Sorghum")], **NO_DAYS),
        ]
        out = asyncio.run(sa.search_advisory(CTX, "aphids in sorghum", crop="Sorghum", season="Rabi"))
        assert len(kp.requests) == 2
        assert "No advisory is available for the Rabi season for Sorghum" in out and "Kharif" in out


class TestCropIsOptional:
    """Option C: no crop -> search anyway; when the advisories found span several crops, answer for each crop and
    ask which crop as the follow-up. A crop the model does pass must still be in the farmer's words (see TestCropGuard)."""

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_advisories_for_several_crops_are_answered_then_ask_which_crop(self, kp, blank):
        kp.response = _respond([_item("c1", crop="Wheat"), _item("c2", crop="Mustard")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(_ctx([_user("how to control aphids?")]), "aphids control", crop=blank))
        followup = _followup(out)
        assert sa.CROP_MARKER in out and "which crop" in followup and "Wheat and Mustard" in followup
        assert "Harvest AKJ-1" in out and out.index("Cite as") < out.index("> Follow-up:")
        assert len(kp.requests) == 1

    def test_nothing_is_said_about_the_place_while_the_crop_is_not_known(self, kp):
        kp.response = _respond(
            [_item("c1", crop="Wheat", location=AGRA), _item("c2", crop="Rice", location=AGRA)], **NO_DAYS
        )
        out = asyncio.run(sa.search_advisory(_ctx([_user("how do I control insect pests in my crop?")]), "insect pests"))
        assert sa.CROP_MARKER in out
        assert "general advice found for your crop" not in out and "not for the farmer's place" not in out

    def test_the_crop_follow_up_is_not_asked_again(self, kp):
        kp.response = _respond([_item("c1", crop="Wheat"), _item("c2", crop="Mustard")], **NO_DAYS)
        history = [_user("how to control aphids?")] + _asked(f"{sa.PENDING_MARKER}{sa.CROP_MARKER} q") + [_user("not sure")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids control"))
        assert "Harvest AKJ-1" in out and sa.PENDING_MARKER not in out

    def test_livestock_question_is_answered_without_a_crop(self, kp):
        kp.response = _respond([_item("c1", crop="Cow"), _item("c2", crop="")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(_ctx([_user("my cow has fever")]), "cow fever"))
        assert "Harvest AKJ-1" in out and sa.PENDING_MARKER not in out

    def test_a_crop_named_in_the_query_counts_as_given(self, kp):
        kp.response = _respond([_item("c1", crop="Wheat"), _item("c2", crop="Mustard")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat"))
        assert "Harvest AKJ-1" in out and "which crop" not in out


class TestGlossaryAndPhoto:
    def test_a_regional_crop_name_is_searched_in_english(self, kp):
        kp.response = _respond([_item("c1", crop="Okra")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "bhindi aphids", crop="bhindi"))
        assert "Harvest AKJ-1" in out
        assert json.loads(kp.requests[0].read())["query"] == "Okra aphids"

    def test_a_photo_goes_to_npss_and_skips_the_advisory_search(self, kp, monkeypatch):
        async def fake_npss(ctx, image_url):
            return f"**NPSS Analysis Result** for {image_url}"

        monkeypatch.setattr(sa, "analyze_crop_image", fake_npss)
        out = asyncio.run(sa.search_advisory(CTX, "pest in photo", image_url="http://img/1"))
        assert out == "**NPSS Analysis Result** for http://img/1"
        assert kp.requests == []


class TestFailures:
    def test_provider_message_is_used_when_not_successful(self, kp):
        kp.response = _respond([], status="error", message="Advisory search failed")
        assert asyncio.run(sa.search_advisory(CTX, "q", crop="Sorghum")) == "Advisory search failed"

    def test_non_200_means_unavailable(self, kp):
        kp.response = httpx.Response(502, text="bad gateway")
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q", crop="Sorghum")).lower()

    def test_missing_kp_url_is_a_configuration_message(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_PROVIDER_URL", raising=False)
        assert "not configured" in asyncio.run(sa.search_advisory(CTX, "q", crop="Sorghum")).lower()

    def test_network_error_means_unavailable(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_PROVIDER_URL", "http://kp.test")
        real = httpx.AsyncClient

        def boom(request):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(sa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom)))
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q", crop="Sorghum")).lower()


def test_tool_is_registered_with_context():
    from agents.tools import TOOLS

    registered = {tool.name: tool for tool in TOOLS}
    assert "search_advisory" in registered
    assert registered["search_advisory"].takes_ctx is True
    schema = registered["search_advisory"].function_schema.json_schema
    assert schema["required"] == ["query"]
    assert "image_url" in schema["properties"]
    assert "advice_type" not in schema["properties"]
    assert {"crop", "season", "days_after_sowing"} <= set(schema["properties"])
    assert "answer_now" not in schema["properties"]
    assert {"search_documents", "search_schemes"} <= registered.keys()
    assert "profile" not in registered["search_documents"].function_schema.json_schema["properties"]
    for merged in ("search_pests_diseases", "search_video", "search_terms", "analyze_crop_image"):
        assert merged not in registered




def test_payload_carries_the_season():
    payload = sa.build_advisory_search_payload("wheat aphids", crop="Wheat", season="Rabi")
    assert payload["advisory"] == {"crop": "Wheat", "season": "Rabi"}


# ---------------------------------------------------------------------------
# Location: ask when the advisories are tied to places, send codes, explain the match
# ---------------------------------------------------------------------------

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart  # noqa: E402

AGRA = {"match": "not_requested", "national": False, "states": ["Uttar Pradesh"], "districts": ["Agra"]}


def _user(text):
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _asked(content):
    return [
        ModelResponse(parts=[ToolCallPart(tool_name="search_advisory", args={})]),
        ModelRequest(parts=[ToolReturnPart(tool_name="search_advisory", tool_call_id="c", content=content)]),
        ModelResponse(parts=[TextPart(content="question")]),
    ]


def _located(chunk_id="c1", location=None, season="Rabi"):
    return _chunk(chunk_id, season, location=location if location is not None else AGRA)


class TestLocationQuestion:
    def test_the_place_follow_up_comes_after_season_and_days_when_advisories_are_tied_to_places(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids, Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi"))
        assert sa.PENDING_MARKER in out and sa.LOCATION_MARKER in out
        assert "state and district" in _followup(out)
        assert "Scout for aphids" in out and out.index("Cite as") < out.index("> Follow-up:")
        # the call to make next restates what the farmer already gave
        assert '`season`="Rabi"' in _followup(out)

    def test_season_days_and_place_are_asked_in_one_follow_up(self, kp):
        kp.response = _respond([_located()], has_day_range="true")
        ctx = _ctx([_user("How can I control aphids in my wheat crop?")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat"))
        assert sa.SEASON_MARKER in out and sa.LOCATION_MARKER in out
        followup = _followup(out)
        assert ("Could you please tell me which season you are growing your crop in, how many days ago you sowed it, "
                "and which state and district you are farming in?") in followup
        assert "Never split it into separate questions" in followup
        assert "`season`, `days_after_sowing` and `state` and `district` (in English)" in followup

    def test_no_place_question_when_every_advisory_applies_everywhere(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids, Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi"))
        assert sa.PENDING_MARKER not in out and "Scout for aphids" in out

    def test_a_chunk_without_location_tags_counts_as_applying_everywhere(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat", crop="Wheat", season="Rabi"))
        assert sa.LOCATION_MARKER not in out

    def test_no_place_question_once_the_farmer_gave_the_place(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids in Agra, Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", district="Agra"))
        assert sa.PENDING_MARKER not in out and "Scout for aphids" in out

    def test_the_place_follow_up_is_not_asked_again_when_the_farmer_does_not_answer_it(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        history = [_user("wheat aphids")] + _asked(f"{sa.PENDING_MARKER}{sa.LOCATION_MARKER} q") + [_user("I do not know")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi"))
        assert sa.PENDING_MARKER not in out and "Scout for aphids" in out
        assert '"This advice is for your Rabi wheat crop. It is not specific to your area."' in out
        assert "meant for" not in out and "could not match" not in out

    def test_a_place_the_lookup_cannot_match_is_not_asked_for_again(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids in Pune, Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", location="Pune"))
        assert sa.PENDING_MARKER not in out and "I could not match Pune to a state or district" in out


class TestPlaceIsSentAsCodes:
    def test_the_state_and_district_the_farmer_gave_are_sent_as_lgd_codes(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids in Agra, Uttar Pradesh, Rabi")])
        asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", state="Uttar Pradesh", district="Agra"))
        assert _params(_sent(kp, 0)) == {"season": "Rabi", "top_k": "5", "state_lgd_code": "9", "district_lgd_code": "118"}

    def test_a_district_alone_also_gives_its_state(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids in Agra, Rabi")])
        asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", district="Agra"))
        params = _params(_sent(kp, 0))
        assert params["state_lgd_code"] == "9" and params["district_lgd_code"] == "118"

    def test_the_answer_to_the_place_question_is_accepted(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "matched"})], **NO_DAYS)
        history = (
            [_user("wheat aphids, Rabi")]
            + _asked(f"{sa.PENDING_MARKER}{sa.LOCATION_MARKER} q")
            + [_user("Agra, Uttar Pradesh")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", state="Uttar Pradesh", district="Agra")
        )
        assert _params(_sent(kp, 0))["district_lgd_code"] == "118"
        assert "Scout for aphids" in out
        assert "This advice is for your district, Agra, in Uttar Pradesh." in out

    def test_a_place_the_model_made_up_is_ignored_and_the_farmer_is_asked(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        ctx = _ctx([_user("How can I control aphids in my wheat crop? Rabi season")])
        out = asyncio.run(
            sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", state="Uttar Pradesh", district="Agra")
        )
        assert "state_lgd_code" not in kp.requests[0].read().decode()
        assert sa.LOCATION_MARKER in out

    def test_a_place_in_an_earlier_message_does_not_count_for_a_new_question(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        prefix = "**Conversation**\n\nUser: wheat aphids in Agra\n\n---\n\n"
        ctx = _ctx([_user(prefix + "When should I harvest wheat? Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "wheat harvest", crop="Wheat", season="Rabi", district="Agra"))
        assert "state_lgd_code" not in kp.requests[0].read().decode()
        assert sa.LOCATION_MARKER in out

    def test_a_place_the_farmer_did_not_write_is_not_mentioned_either(self, kp):
        kp.response = _respond([_chunk("c1", "Rabi")], **NO_DAYS)
        ctx = _ctx([_user("wheat aphids, Rabi")])
        out = asyncio.run(sa.search_advisory(ctx, "aphids in wheat", crop="Wheat", season="Rabi", location="Pune"))
        assert "Pune" not in out


class TestLocationNotes:
    def _notes(self, results, place=None, named=""):
        return "\n".join(sa.format_location_notes(results, place, named))

    def _result(self, **kw):
        base = {"title": "Wheat Insect Management", "location_national": False, "location_match": "not_requested",
                "location_states": ["Uttar Pradesh"], "location_districts": ["Agra"]}
        return {**base, **kw}

    def test_the_sentence_is_the_very_first_line_and_no_outside_advice_is_allowed(self):
        from helpers.locations import resolve_place

        out = self._notes([self._result(location_match="matched")], resolve_place("Agra"))
        assert "very first line" in out and "do not add steps or details from your own knowledge" in out

    def test_matched(self):
        from helpers.locations import resolve_place

        opening = sa.location_opening([self._result(location_match="matched")], resolve_place("Agra"))
        assert opening == "This advice is for your district, Agra, in Uttar Pradesh."

    def test_matched_on_a_statewide_record_names_the_state(self):
        from helpers.locations import resolve_place

        statewide = self._result(location_match="matched", location_states=["Tamil Nadu"], location_districts=[])
        opening = sa.location_opening([statewide], resolve_place("Tamil Nadu"))
        assert opening == "This advice covers all of Tamil Nadu."

    def test_partial_names_the_districts(self):
        from helpers.locations import resolve_place

        opening = sa.location_opening([self._result(location_match="state_partial")], resolve_place("Uttar Pradesh"))
        assert opening == "This advice is for some districts of Uttar Pradesh: Agra."

    def test_advice_for_other_places_is_offered_as_general_advice_without_naming_those_places(self):
        from helpers.locations import resolve_place

        maize = self._result(location_match="other", location_states=["West Bengal"], location_districts=["Bankura", "Birbhum"])
        opening = sa.location_opening([maize], resolve_place("Tamil Nadu"))
        assert opening == "There is no advisory specific to Tamil Nadu. This is the general advice found for your crop."
        assert "Bankura" not in "\n".join(sa.format_location_notes([maize], resolve_place("Tamil Nadu")))

    def test_other_places_next_to_general_advice(self):
        from helpers.locations import resolve_place

        results = [self._result(location_match="other"), {"title": "Gen", "location_national": True}]
        opening = sa.location_opening(results, resolve_place("Punjab"))
        assert opening == "There is no advisory specific to Punjab. This is the general advice found for your crop."

    def test_general_advice_is_not_specific_to_the_place(self):
        from helpers.locations import resolve_place

        opening = sa.location_opening([{"title": "T", "location_national": True}], resolve_place("Agra"))
        assert opening == "There is no advisory specific to Agra, Uttar Pradesh. This is the general advice found for your crop."

    def test_general_advice_without_a_place_says_nothing_about_location(self):
        assert self._notes([{"title": "T", "location_national": True}]) == ""

    def test_a_mix_of_located_and_general_advisories(self):
        from helpers.locations import resolve_place

        results = [self._result(location_match="matched"), {"title": "Other", "location_national": True}]
        out = self._notes(results, resolve_place("Agra"))
        assert "'Other' applies everywhere" in out and "This advice is for your district, Agra, in Uttar Pradesh." in out

    def test_the_other_places_case_reaches_the_agent_through_the_tool(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "other"})], **NO_DAYS)
        history = (
            [_user("wheat aphids, Rabi")]
            + _asked(f"{sa.PENDING_MARKER}{sa.LOCATION_MARKER} q")
            + [_user("Tamil Nadu")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "aphids in wheat", crop="Wheat", season="Rabi", state="Tamil Nadu")
        )
        assert "There is no advisory specific to Tamil Nadu. This is the general advice found for your crop." in out
        assert "Agra" not in out.split("Open your answer")[1]
        assert "Scout for aphids" in out


class TestPlaceComesFromTheFarmersWords:
    def test_a_place_carried_over_by_the_model_does_not_replace_the_farmers_answer(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "other"})], **NO_DAYS)
        history = (
            [_user("wheat nursery bed, Rabi")]
            + _asked(f"{sa.PENDING_MARKER}{sa.LOCATION_MARKER} q")
            + [_user("Bareilly, Uttar Pradesh")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "wheat nursery bed", crop="Wheat", season="Rabi", state="Tamil Nadu")
        )
        assert _params(_sent(kp, 0))["district_lgd_code"] == "130"
        assert "Tamil Nadu" not in out

    def test_an_answer_the_lookup_does_not_know_is_named_not_replaced_by_an_earlier_place(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "other"})], **NO_DAYS)
        history = (
            [_user("wheat nursery bed, Rabi")]
            + _asked(f"{sa.PENDING_MARKER}{sa.LOCATION_MARKER} q")
            + [_user("dharwad")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "wheat nursery bed", crop="Wheat", season="Rabi", state="Tamil Nadu")
        )
        assert "state_lgd_code" not in kp.requests[0].read().decode()
        assert "I could not match dharwad to a state or district. This is the general advice found for your crop." in out
        assert "Tamil Nadu" not in out and sa.PENDING_MARKER not in out


class TestPlaceIsNotAskedTwice:
    """The place the farmer gave while a question was being worked on still counts in later turns of that question."""

    def test_a_place_given_before_the_season_and_days_answer_is_not_asked_for_again(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "other"})], **NO_DAYS)
        history = (
            [_user("seed treatment for wheat")]
            + _asked(f"{sa.PENDING_MARKER} season and days question")
            + [_user("Yadgir, Karnataka")]
            + _asked(f"{sa.PENDING_MARKER} season and days question again")
            + [_user("Rabi, 60 days")]
        )
        out = asyncio.run(
            sa.search_advisory(
                _ctx(history), "seed treatment", crop="Wheat", season="Rabi", days_after_sowing=60,
                state="Karnataka", district="Yadgir",
            )
        )
        assert sa.LOCATION_MARKER not in out and "Scout for aphids" in out
        assert _params(_sent(kp, 0))["district_lgd_code"] == "635"

    def test_the_model_passing_nothing_still_finds_the_place_the_farmer_gave_earlier_in_the_question(self, kp):
        kp.response = _respond([_located(location={**AGRA, "match": "other"})], **NO_DAYS)
        history = (
            [_user("seed treatment for wheat")]
            + _asked(f"{sa.PENDING_MARKER} season and days question")
            + [_user("Yadgir, Karnataka")]
            + _asked(f"{sa.PENDING_MARKER} season and days question again")
            + [_user("Rabi, 60 days")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "seed treatment", crop="Wheat", season="Rabi", days_after_sowing=60)
        )
        assert sa.LOCATION_MARKER not in out
        assert _params(_sent(kp, 0))["district_lgd_code"] == "635"

    def test_a_place_from_an_earlier_finished_question_does_not_carry_over(self, kp):
        kp.response = _respond([_located()], **NO_DAYS)
        history = (
            [_user("seed treatment for wheat in Yadgir, Karnataka")]
            + [ModelResponse(parts=[TextPart(content="Advice")])]
            + [_user("seed treatment for wheat, Rabi")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "seed treatment", crop="Wheat", season="Rabi", district="Yadgir")
        )
        assert sa.LOCATION_MARKER in out


class TestPlaceSentenceWording:
    def _result(self, districts, states=("Karnataka",), match="matched"):
        return {"title": "T", "location_national": False, "location_match": match,
                "location_states": list(states), "location_districts": list(districts)}

    def test_a_long_district_list_is_not_read_out_for_a_matched_district(self):
        from helpers.locations import resolve_place

        many = ["Ernakulam", "Idukki", "Kannur", "Kasaragod", "Vijayapura", "Yadgir"]
        opening = sa.location_opening([self._result(many)], resolve_place("Yadgir, Karnataka"))
        assert opening == "This advice is for your district, Yadgir, in Karnataka."
        assert "Ernakulam" not in opening

    def test_a_long_partial_list_is_shortened(self):
        from helpers.locations import resolve_place

        many = ["Ernakulam", "Idukki", "Kannur", "Kasaragod", "Vijayapura", "Yadgir"]
        opening = sa.location_opening([self._result(many, match="state_partial")], resolve_place("Karnataka"))
        assert opening == "This advice is for some districts of Karnataka: Ernakulam, Idukki, Kannur, Kasaragod and 2 more."

    def test_a_few_partial_districts_are_all_named(self):
        from helpers.locations import resolve_place

        opening = sa.location_opening(
            [self._result(["Agra", "Aligarh", "Azamgarh"], ("Uttar Pradesh",), "state_partial")], resolve_place("Uttar Pradesh")
        )
        assert opening == "This advice is for some districts of Uttar Pradesh: Agra, Aligarh and Azamgarh."


class TestCoffeePdfAnswersGenerically:
    """An uploaded coffee PDF (origin docs-pipeline, crop Coffee) is answered at once: no season, days or place question."""

    @staticmethod
    def _pdf(chunk_id="p1", season="Rabi", **kw):
        return _chunk(chunk_id, season, crop="Coffee", title="coffee_guide_2023_mini", origin="docs-pipeline", **kw)

    def test_no_season_days_or_place_question(self, kp):
        located = {"match": "not_requested", "national": False, "states": ["Karnataka"], "districts": ["Kodagu"]}
        kp.response = _respond([self._pdf("p1", "Rabi", location=located), self._pdf("p2", "Kharif")], has_day_range="true")
        out = asyncio.run(sa.search_advisory(CTX, "leaf rust", crop="Coffee"))
        assert "Scout for aphids (p1)" in out and "Source: coffee_guide_2023_mini" in out
        assert sa.PENDING_MARKER not in out and "ask" not in out.lower().split("search notes")[0]
        assert "advisory_has_day_range=no" in out
        assert "applies everywhere" not in out and "This advice is for" not in out
        assert len(kp.requests) == 1

    def test_a_season_the_farmer_gave_does_not_trigger_a_requery_or_suggestion(self, kp):
        kp.response = _respond([self._pdf("p1", "Rabi")], has_day_range="true")
        out = asyncio.run(sa.search_advisory(_ctx([_user("coffee leaf rust, it is kharif season")]), "leaf rust", crop="Coffee", season="Kharif"))
        assert "Scout for aphids (p1)" in out and len(kp.requests) == 1

    def test_a_coffee_chunk_from_kisan_sarathi_still_asks(self, kp):
        kp.response = _respond([_chunk("w1", "Rabi", crop="Coffee", origin="webhook")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "leaf rust", crop="Coffee"))
        assert "Scout for aphids (w1)" in out and sa.SEASON_MARKER in out

    def test_a_pdf_of_another_crop_still_asks(self, kp):
        kp.response = _respond([_chunk("w1", "Rabi", crop="Wheat", origin="docs-pipeline")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "aphids", crop="Wheat"))
        assert "Scout for aphids (w1)" in out and sa.SEASON_MARKER in out

    def test_coffee_pdf_chunks_win_over_other_coffee_chunks(self, kp):
        kp.response = _respond([_chunk("w1", "Rabi", crop="Coffee", origin="webhook"), self._pdf("p1")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(CTX, "leaf rust", crop="Coffee"))
        assert "(p1)" in out and "(w1)" not in out


class TestCropGuard:
    """The crop must come from the farmer's words for this question."""

    @staticmethod
    def _earlier_answer():
        return [_user("wheat aphids"), ModelResponse(parts=[TextPart(content="answer")])]

    def test_a_crop_taken_from_an_earlier_question_is_refused(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        history = self._earlier_answer() + [_user("how to manage mealybugs")]
        with pytest.raises(ModelRetry, match="not in the farmer's words"):
            asyncio.run(sa.search_advisory(_ctx(history), "mealybug management", crop="Wheat"))
        assert kp.requests == []

    def test_the_refusal_quotes_the_farmers_message_so_the_model_can_retry_with_the_right_crop(self, kp):
        history = self._earlier_answer() + [_user("How do I monitor aphids in my sorghum crop?")]
        with pytest.raises(ModelRetry) as err:
            asyncio.run(sa.search_advisory(_ctx(history), "aphids", crop="Coffee"))
        text = str(err.value)
        assert "How do I monitor aphids in my sorghum crop?" in text and "do not ask which crop" in text

    def test_the_crop_in_the_farmers_message_is_accepted(self, kp):
        kp.response = _respond([_item("c1", crop="Wheat")], **NO_DAYS)
        out = asyncio.run(sa.search_advisory(_ctx([_user("wheat aphids")]), "wheat aphids", crop="Wheat"))
        assert "Search notes" in out

    def test_the_crop_answer_to_a_crop_question_is_accepted(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        history = [_user("aphids"), ModelResponse(parts=[TextPart(content="Which crop?")]), _user("Sorghum")]
        out = asyncio.run(sa.search_advisory(_ctx(history), "aphids", crop="Sorghum"))
        assert "Search notes" in out

    def test_a_season_question_the_model_wrote_itself_keeps_the_crop_of_the_question(self, kp):
        kp.response = _respond([_item("c1", crop="Japanese mint")], **NO_DAYS)
        history = self._earlier_answer() + [
            _user("How should I do seed treatment for Japanese mint?"),
            ModelResponse(parts=[TextPart(content="Could you please tell me which season you are growing it in?")]),
            _user("Zaid, 60 days"),
        ]
        out = asyncio.run(sa.search_advisory(_ctx(history), "seed treatment", crop="Japanese mint"))
        assert "Search notes" in out

    def test_questions_the_model_wrote_itself_keep_the_whole_chain(self):
        from helpers import advisory_pending as ap

        history = [
            _user("How do I manage brown rust in my wheat crop?"),
            ModelResponse(parts=[TextPart(content="Could you please tell me which season you are growing your wheat in, and how many days ago you sowed it?")]),
            _user("Rabi, 20 days"),
            ModelResponse(parts=[TextPart(content="Could you please tell me which state and district you are farming in?")]),
            _user("Nagaland"),
        ]
        ctx = _ctx(history)
        assert ap.asked_in_previous_turn(ctx) and ap.asked_location_in_previous_turn(ctx)
        assert ap.farmer_gave_season(ctx) and ap.farmer_gave_days(ctx) and ap.farmer_gave_place(ctx)
        assert ap.farmer_named_crop(ctx, "Wheat")

    def test_an_answer_with_a_source_line_does_not_continue_the_question(self, kp):
        answer = "Advice.\n\n**Source: ICAR - Kisan Sarathi**\n\nWould you like pest advice for wheat?"
        history = [_user("wheat aphids"), ModelResponse(parts=[TextPart(content=answer)]), _user("yes")]
        with pytest.raises(ModelRetry, match="not in the farmer's words"):
            asyncio.run(sa.search_advisory(_ctx(history), "pest advice", crop="Wheat"))

    def test_crop_words_in_another_script_are_accepted(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        out = asyncio.run(
            sa.search_advisory(_ctx([_user("गेहूं में माहू")]), "wheat aphids", crop="Sorghum", crop_words="गेहूं")
        )
        assert "Search notes" in out

    def test_crop_words_the_farmer_never_wrote_are_refused(self, kp):
        history = self._earlier_answer() + [_user("stem borer")]
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx(history), "stem borer", crop="Wheat", crop_words="wheat"))

    def test_a_crop_only_query_becomes_a_general_crop_search(self, kp):
        kp.response = _respond([_item("c1")], **NO_DAYS)
        history = [_user("aphids"), ModelResponse(parts=[TextPart(content="Which crop?")]), _user("Sorghum")]
        asyncio.run(sa.search_advisory(_ctx(history), "Sorghum", crop="Sorghum"))
        assert "Sorghum crop advisory" in kp.requests[0].read().decode()

    def test_the_standard_name_of_a_crop_the_farmer_called_by_another_name_is_accepted(self, kp):
        # "paddy" in the first question, then only "Kharif, 145 days"; the model passes the standard name Rice
        kp.response = _respond([_chunk("c1", "Kharif", crop="Rice")], **NO_DAYS)
        history = (
            [_user("At what stage should I harvest my paddy crop?")]
            + _asked(f"{sa.PENDING_MARKER} q")
            + [_user("Kharif, 145 days")]
        )
        out = asyncio.run(
            sa.search_advisory(_ctx(history), "paddy harvest stage", crop="Rice", season="Kharif", days_after_sowing=145)
        )
        assert "Search notes" in out

    def test_a_synonym_does_not_let_another_crop_through(self, kp):
        history = [_user("At what stage should I harvest my paddy crop?")] + _asked("answer") + [_user("how to control aphids")]
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx(history), "aphids", crop="Rice"))
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx([_user("paddy harvest")]), "harvest", crop="Wheat"))

    def test_arabica_counts_as_naming_coffee(self, kp):
        kp.response = _respond(
            [_chunk("p1", "Rabi", crop="Coffee", title="coffee_guide_2023_mini", origin="docs-pipeline")], **NO_DAYS
        )
        out = asyncio.run(sa.search_advisory(_ctx([_user("beetle on arabica stems")]), "stem borer", crop="Coffee"))
        assert "Source: coffee_guide_2023_mini" in out

    def test_coffee_is_refused_when_the_farmer_never_named_it(self, kp):
        history = self._earlier_answer() + [_user("how to manage mealybugs")]
        with pytest.raises(ModelRetry):
            asyncio.run(sa.search_advisory(_ctx(history), "mealybug management", crop="Coffee"))
