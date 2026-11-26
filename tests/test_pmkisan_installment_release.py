import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agents.skills import render_prompt
from agents.skill_policy import SkillState
from tests.test_production_reconciliation import load_module
from tests.test_prompt_language_contract import SCRIPT_RANGES, render


@pytest.fixture
def search(load_module, monkeypatch):
    for name, attributes in {
        "marqo": {},
        "qdrant_client": {"QdrantClient": object},
        "qdrant_client.models": {
            "FieldCondition": object, "Filter": object, "MatchValue": object,
        },
        "sentence_transformers": {"SentenceTransformer": object},
        "agents.tools.terms": {"normalize_text_with_glossary": lambda text: text},
        "agents.tools.imd_advisory": {"agromet_note": Mock()},
    }.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**attributes))
    schemes = load_module("helpers/scheme_qdrant_search.py")
    monkeypatch.setitem(sys.modules, "helpers.scheme_qdrant_search", schemes)
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_URL", "https://example.test")
    # A cached service-account token, so the only POST is the search itself.
    from helpers import knowledge_provider
    monkeypatch.setattr(knowledge_provider, "_token", "tok")
    monkeypatch.setattr(knowledge_provider, "_token_expires_at", float("inf"))
    return load_module("agents/tools/search.py")


def network_response(scheme, *, hits=True, status="success", resolved=True):
    """A KP /search response for scheme documents."""
    return {
        "effective_config": {"ranking_score_field": "_scheme_score"},
        "hits": [{"_id": "chunk-1", "chunk_id": "chunk-1", "scheme_code": scheme, "scheme_name": scheme,
                  "text": "Scheme details", "_scheme_score": 0.8}] if hits else [],
        "scheme_search": {"status": status, "resolved_scheme_code": scheme if resolved else None},
    }


def run_search(search, monkeypatch, response, query="PM-KISAN 24th installment release date"):
    client = AsyncMock()
    client.__aenter__.return_value = client
    if isinstance(response, Exception):
        client.post.side_effect = response
    else:
        client.post.return_value = response
    monkeypatch.setattr(search.httpx, "AsyncClient", lambda: client)
    result = asyncio.run(search.search_schemes(query))
    return result, client.post


@pytest.mark.parametrize("scheme,resolved", [
    ("pmkisan", True), ("pmkisan", False), ("mif", True),
])
def test_search_does_not_append_system_announcement(search, monkeypatch, scheme, resolved):
    result, post = run_search(
        search, monkeypatch,
        httpx.Response(200, json=network_response(scheme, resolved=resolved)),
        f"{scheme} 24th installment release date",
    )
    assert "Scheme details" in result
    assert "PM-KISAN 24th Instalment Release" not in result
    assert "30th October" not in result
    if scheme == "pmkisan" and resolved:
        assert "Status check available" in result
    assert "23rd" not in result and "20 June 2026" not in result
    assert post.call_args.args[0] == "https://example.test/search"
    # Plain semantic search: no scheme code filter is sent.
    assert post.call_args.kwargs["json"]["document_kind"] == "scheme"
    assert "document_code" not in post.call_args.kwargs["json"]


@pytest.mark.parametrize("response", [
    httpx.Response(503),
    httpx.Response(200, json=[]),
    httpx.Response(200, json=network_response("pmkisan", hits=False)),
    httpx.Response(200, json=network_response("pmkisan", hits=False, status="unavailable")),
    httpx.Response(200, json=network_response("pmkisan", status="unavailable")),
    httpx.ReadTimeout("test timeout"),
    httpx.ConnectError("test connection"),
])
def test_search_failures_do_not_claim_release(search, monkeypatch, response):
    result, _ = run_search(search, monkeypatch, response)
    assert result
    assert "PM-KISAN 24th Instalment Release" not in result
    assert "30th October" not in result


def test_missing_endpoint_does_not_claim_release(search, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_PROVIDER_URL")
    result, post = run_search(search, monkeypatch, httpx.Response(200))
    assert "not configured" in result
    post.assert_not_called()


def test_unknown_scheme_is_still_searched_semantically(search, monkeypatch):
    result, post = run_search(
        search, monkeypatch,
        httpx.Response(200, json=network_response("pmkisan", hits=False)),
        "unknown scheme",
    )
    assert result
    post.assert_called_once()


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
def test_release_announcement_skill_needs_no_business_tools(language):
    state = SkillState(enabled_skills=["pm_kisan.installments"])
    prompt = render_prompt(state, language, {"today_date": "28 October 2026"})
    assert "The 24th instalment of PM-KISAN was released on 30th October 2026." in prompt
    assert "Do not call search_schemes, load_skill or any status/OTP tool" in prompt
    assert not state.visible


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
def test_release_and_status_rules_remain_present_in_default_mode(language):
    prompt = render_prompt(SkillState(), language, {})
    assert "Do not continue a pending request for identifiers" in prompt
    assert "A previous status-check offer is not consent" in prompt
    assert "Before any PM-KISAN tool call" not in prompt
    assert "does not apply to scheme information or public release dates" in prompt
    assert "The 24th instalment of PM-KISAN was released on 30th October 2026." in prompt
    assert "Do not call search_schemes, load_skill or any status/OTP tool" in prompt


def test_removed_legacy_tool_is_not_registered():
    from pathlib import Path
    from agents.skill_catalog import get_catalog
    root = Path(__file__).resolve().parents[1]
    assert not (root / "agents/tools/scheme_info.py").exists()
    assert "get_scheme_info" not in get_catalog().tools
    assert "get_scheme_info" not in (root / "agents/tools/__init__.py").read_text()


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
@pytest.mark.parametrize("today", ["28 October 2026", "31 October 2026", "5 November 2026"])
def test_release_is_static_and_past_tense(language, today):
    prompt = render_prompt(SkillState(enabled_skills=["pm_kisan.installments"]), language,
                           {"today_date": today})
    assert prompt.count("## PM-KISAN 24th Instalment Release") == 1
    assert "**Status:** released" in prompt
    assert "The 24th instalment of PM-KISAN was released on 30th October 2026." in prompt
    assert "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।" in prompt
    assert "will be released on" not in prompt
    assert "जारी की जाएगी" not in prompt
    assert "future tense" not in prompt
    assert "**From 31 October 2026 (IST):**" not in prompt
