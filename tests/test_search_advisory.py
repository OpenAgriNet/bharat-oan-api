"""Tests for the search_advisory tool (BAP knowledge-advisory search)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agents.tools import search_advisory as sa

# The provider appends the Source line to the stored chunk text.
TEXT = "Advisory: Harvest AKJ-1.\n\nSource: ICAR–DKMA, jak"

CTX = SimpleNamespace(deps=SimpleNamespace(session_id="sess-1", question_id="q-1"))


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


def _on_search(items, status="success", message="", query="aphids"):
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
        assert "Source: ICAR–DKMA" in out


class TestTool:
    def test_posts_to_bap_search_and_returns_formatted_advice(self, bap):
        out = asyncio.run(sa.search_advisory(CTX, "aphids in wheat"))
        request = bap.requests[0]
        assert str(request.url) == "http://bap.test/search"
        body = request.read().decode()
        assert '"knowledge-advisory"' in body and "aphids in wheat" in body
        assert "Harvest AKJ-1" in out and "ICAR" in out

    def test_top_k_limits_results(self, bap):
        bap.response = httpx.Response(200, json=_on_search([_item(f"c{i}", title=f"T{i}") for i in range(6)]))
        out = asyncio.run(sa.search_advisory(CTX, "q", top_k=2))
        assert "T0" in out and "T1" in out and "T2" not in out

    def test_empty_responses_say_nothing_was_found(self, bap):
        bap.response = httpx.Response(200, json={"context": {}, "responses": []})
        out = asyncio.run(sa.search_advisory(CTX, "something odd"))
        assert "No advisory" in out and "something odd" in out

    def test_provider_message_is_used_when_not_successful(self, bap):
        bap.response = httpx.Response(200, json=_on_search([], status="error", message="Advisory search failed"))
        assert asyncio.run(sa.search_advisory(CTX, "q")) == "Advisory search failed"

    def test_non_200_means_unavailable(self, bap):
        bap.response = httpx.Response(502, text="bad gateway")
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q")).lower()

    def test_missing_bap_endpoint_is_a_configuration_message(self, monkeypatch):
        monkeypatch.delenv("BAP_ENDPOINT", raising=False)
        assert "not configured" in asyncio.run(sa.search_advisory(CTX, "q")).lower()

    def test_network_error_means_unavailable(self, monkeypatch):
        monkeypatch.setenv("BAP_ENDPOINT", "http://bap.test")
        real = httpx.AsyncClient

        def boom(request):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(sa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom)))
        assert "unavailable" in asyncio.run(sa.search_advisory(CTX, "q")).lower()
