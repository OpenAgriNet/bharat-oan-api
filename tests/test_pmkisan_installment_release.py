import asyncio
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agents.skills import render_prompt
from agents.skill_policy import SkillState
from helpers import pmkisan_installment_release as release
from tests.test_production_reconciliation import load_module
from tests.test_prompt_language_contract import SCRIPT_RANGES, render


@pytest.mark.parametrize("today,future", [
    (date(2026, 10, 28), True),
    (date(2026, 10, 30), True),
    (date(2026, 10, 31), False),
])
def test_release_messages_and_section(today, future):
    en, hi = release.get_pm_kisan_24th_installment_release_messages(today)
    assert en == (
        "The 24th instalment of PM-KISAN will be released on 30th October 2026."
        if future else
        "The 24th instalment of PM-KISAN was released on 30th October 2026."
    )
    assert hi == (
        "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।"
        if future else
        "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।"
    )
    section = release.get_pm_kisan_24th_installment_release_section(today)
    assert "## PM-KISAN 24th Instalment Release" in section
    assert "**Instalment number:** 24" in section
    assert "**Official release date:** 30 October 2026" in section
    assert f"**Status as of today:** {'upcoming' if future else 'released'}" in section
    assert en in section and hi in section


@pytest.mark.parametrize("minute,future", [(29, True), (30, False)])
def test_default_date_switches_at_ist_midnight(monkeypatch, minute, future):
    instant = datetime(2026, 10, 30, 18, minute, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.side_effect = lambda tz: instant.astimezone(tz)
    monkeypatch.setattr(release, "datetime", clock)
    en, _ = release.get_pm_kisan_24th_installment_release_messages()
    assert ("will be released" in en) is future
    assert en in release.get_pm_kisan_24th_installment_release_section()
    assert str(clock.now.call_args.args[0]) == "Asia/Kolkata"


@pytest.fixture
def search(load_module, monkeypatch):
    entries = [
        {"code": "pmkisan", "name": "PM-KISAN", "aliases": ["PM kisan"]},
        {"code": "mif", "name": "Micro Irrigation Fund", "aliases": []},
    ]
    for name, attributes in {
        "marqo": {},
        "qdrant_client": {"QdrantClient": object},
        "qdrant_client.models": {
            "FieldCondition": object, "Filter": object, "MatchValue": object,
        },
        "sentence_transformers": {"SentenceTransformer": object},
        "helpers.master_catalog": {
            "_tier_for_environment": Mock(),
            "get_master_catalog_snapshot": Mock(),
            "get_vector_scheme_entries": lambda: entries,
        },
        "agents.tools.terms": {"normalize_text_with_glossary": lambda text: text},
        "agents.tools.imd_advisory": {"agromet_note": Mock()},
    }.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**attributes))
    schemes = load_module("helpers/scheme_qdrant_search.py")
    monkeypatch.setitem(sys.modules, "helpers.scheme_qdrant_search", schemes)
    monkeypatch.setenv("BAP_ENDPOINT", "https://example.test")
    monkeypatch.setattr(release, "_today_ist", lambda: date(2026, 10, 28))
    return load_module("agents/tools/search.py")


def network_response(scheme, *, hits=True, status="success", resolved=True):
    context = {"status": status}
    if resolved:
        context["resolved-scheme-code"] = scheme
    return {"responses": [{"message": {"catalog": {
        "tags": [{
            "descriptor": {"code": "search-context"},
            "list": [{"descriptor": {"code": key}, "value": value}
                     for key, value in context.items()],
        }],
        "providers": [{"items": [{
            "id": "chunk-1",
            "descriptor": {"code": scheme, "name": scheme, "long_desc": "Scheme details"},
        }] if hits else []}],
    }}}]}


def run_search(search, monkeypatch, response, query="PM-KISAN 24th installment release date"):
    client = AsyncMock()
    client.__aenter__.return_value = client
    if isinstance(response, Exception):
        client.post.side_effect = response
    else:
        client.post.return_value = response
    monkeypatch.setattr(search.httpx, "AsyncClient", lambda: client)
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test", question_id="test"))
    result = asyncio.run(search.search_schemes(ctx, query))
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
    if scheme == "pmkisan":
        assert "Status check available" in result
    assert "23rd" not in result and "20 June 2026" not in result
    assert post.call_args.args[0] == "https://example.test/search"
    assert post.call_args.kwargs["json"]["message"]["intent"]["item"]["descriptor"]["code"] == scheme


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
    monkeypatch.delenv("BAP_ENDPOINT")
    result, post = run_search(search, monkeypatch, httpx.Response(200))
    assert "not configured" in result
    post.assert_not_called()


def test_unknown_scheme_is_not_sent_to_network(search, monkeypatch):
    result, post = run_search(search, monkeypatch, httpx.Response(200), "unknown scheme")
    assert "Scheme not available right now" in result
    post.assert_not_called()


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
def test_release_announcement_skill_needs_no_business_tools(language):
    state = SkillState(enabled_skills=["pm_kisan.installments"])
    prompt = render_prompt(state, language, {"today_date": "28 October 2026"})
    assert "The 24th instalment of PM-KISAN will be released on 30th October 2026." in prompt
    assert "Do not call search_schemes, load_skill or any status/OTP tool" in prompt
    assert not state.visible


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
def test_release_and_status_rules_remain_present_in_default_mode(language):
    prompt = render_prompt(SkillState(), language, {})
    assert "Do not continue a pending request for identifiers" in prompt
    assert "A previous status-check offer is not consent" in prompt
    assert "Before any PM-KISAN tool call" not in prompt
    assert "does not apply to scheme information or public release dates" in prompt
    assert "The 24th instalment of PM-KISAN will be released on 30th October 2026." in prompt
    assert "Do not call search_schemes, load_skill or any status/OTP tool" in prompt


def test_removed_legacy_tool_is_not_registered():
    from pathlib import Path
    from agents.skill_catalog import get_catalog
    root = Path(__file__).resolve().parents[1]
    assert not (root / "agents/tools/scheme_info.py").exists()
    assert "get_scheme_info" not in get_catalog().tools
    assert "get_scheme_info" not in (root / "agents/tools/__init__.py").read_text()


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
@pytest.mark.parametrize("today", [date(2026, 10, 28), date(2026, 10, 31)])
def test_configured_release_replaces_fallback(language, today):
    section = release.get_pm_kisan_24th_installment_release_section(today)
    prompt = render_prompt(SkillState(), language, {"pm_kisan_installment_release": section})
    assert prompt.count(section) == 1
    en, hi = release.get_pm_kisan_24th_installment_release_messages(today)
    assert en in prompt and hi in prompt
    assert "**Through 30 October 2026 (IST):**" not in prompt
