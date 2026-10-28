import asyncio
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agents.capabilities import capability_prompt_context
from agents.capability_policy import CapabilityState, ToolPolicy
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
        "The 24th installment of PM kisan will release on 30th October 2026."
        if future else
        "The 24th installment of PM kisan was released on 30th October 2026."
    )
    assert hi == (
        "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।"
        if future else
        "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।"
    )
    section = release.get_pm_kisan_24th_installment_release_section(today)
    assert "## PM-KISAN 24th Installment Release" in section
    assert "**Installment number:** 24" in section
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
    assert ("will release" in en) is future
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
def test_search_returns_release_only_for_pmkisan(search, monkeypatch, scheme, resolved):
    result, post = run_search(
        search, monkeypatch,
        httpx.Response(200, json=network_response(scheme, resolved=resolved)),
        f"{scheme} 24th installment release date",
    )
    assert "Scheme details" in result
    assert ("PM-KISAN 24th Installment Release" in result) is (scheme == "pmkisan")
    if scheme == "pmkisan":
        assert result.index("PM-KISAN 24th Installment Release") < result.index("Status check available")
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
    assert "PM-KISAN 24th Installment Release" not in result
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
def test_release_announcement_needs_no_loaded_tools(language):
    for tools in [
        set(), {"search_schemes"},
        {"initiate_pm_kisan_status_check", "check_pm_kisan_status_with_otp"},
    ]:
        deps = SimpleNamespace(capabilities=CapabilityState(policy=ToolPolicy(
            allowed_tools=tools, preloaded_tools=frozenset(),
        )))
        context = capability_prompt_context(deps, f"agrinet_{language}", {"today_date": "28 October 2026"})
        prompt = render(language, **context)
        assert "The 24th installment of PM kisan will release on 30th October 2026." in prompt
        assert "Do not call search_schemes, load_capability or any status/OTP tool" in prompt
        assert not deps.capabilities.visible
        assert not deps.capabilities.loaded_tools


@pytest.mark.parametrize("language", ["en", *SCRIPT_RANGES])
def test_release_routing_remains_visible_after_status_capability_was_loaded(language):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    status_tools = {"initiate_pm_kisan_status_check", "check_pm_kisan_status_with_otp"}
    deps = SimpleNamespace(capabilities=CapabilityState(policy=ToolPolicy(
        allowed_tools=status_tools | {"search_schemes"}, preloaded_tools=frozenset(),
    )))
    deps.capabilities.restore([ModelRequest(parts=[ToolReturnPart(
        tool_name="load_capability",
        content={"capability": "pmkisan", "loaded_tools": sorted(status_tools)},
        tool_call_id="previous-status-check",
    )])])
    context = capability_prompt_context(deps, f"agrinet_{language}", {})
    prompt = render(language, **context)
    assert "Do not continue a pending request for identifiers" in prompt
    assert "A previous status-check offer is not consent" in prompt
    assert "Before any PM-KISAN tool call" not in prompt
    assert "does not apply to scheme information or public release dates" in prompt
    assert "The 24th installment of PM kisan will release on 30th October 2026." in prompt
    assert "Do not call search_schemes, load_capability or any status/OTP tool" in prompt
    assert "search_schemes" not in deps.capabilities.visible


@pytest.mark.parametrize("scheme_code", ["pmkisan", "kcc"])
def test_legacy_scheme_information_stays_available(load_module, monkeypatch, scheme_code):
    scheme = load_module("agents/tools/scheme_info.py")
    context = {
        "ttl": "PT10M", "action": "search", "timestamp": "2026-10-28T00:00:00Z",
        "message_id": "test", "transaction_id": "test", "domain": "schemes:vistaar",
        "version": "1.1.0",
    }
    data = {"context": context, "responses": [{
        "context": context,
        "message": {"catalog": {"descriptor": {}, "providers": [{
            "descriptor": {}, "items": [{
                "id": scheme_code, "descriptor": {"name": scheme_code},
                "tags": [{"descriptor": {}, "list": [{
                    "descriptor": {"name": "Scheme Eligibility"},
                    "value": "Eligible farmers.",
                }]}],
            }],
        }]}},
    }]}
    monkeypatch.setenv("BAP_ENDPOINT", "https://example.test")
    post = Mock(return_value=httpx.Response(200, json=data))
    monkeypatch.setattr(scheme.httpx, "post", post)
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test", question_id="test"))
    result = scheme.get_scheme_info(ctx, scheme_code)
    assert "Scheme Eligibility" in result and "Eligible farmers." in result
    assert "Installment Release" not in result and "Instalment Release" not in result
    assert post.call_args.kwargs["json"]["message"]["intent"]["item"]["descriptor"]["name"] == scheme_code
