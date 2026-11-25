"""helpers.knowledge_provider: service-account token caching and the single re-auth on 401."""

import asyncio

import httpx
import pytest

from helpers import knowledge_provider as kp


@pytest.fixture
def fake_kp(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_URL", "http://kp.test/api/")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_TOKEN_URL", "http://kp.test/token")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_CLIENT_ID", "client")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_CLIENT_SECRET", "secret")
    monkeypatch.delenv("KNOWLEDGE_PROVIDER_NETWORK_ID", raising=False)
    monkeypatch.setattr(kp, "_token", None)
    calls = {"token": 0, "search": [], "search_status": [], "token_response": None, "search_body": {"hits": []}}
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path == "/token":
            calls["token"] += 1
            assert b"grant_type=client_credentials" in request.read()
            if calls["token_response"] is not None:
                return calls["token_response"]
            return httpx.Response(200, json={"access_token": f"tok{calls['token']}", "expires_in": 300})
        calls["search"].append(request.headers["Authorization"])
        status = calls["search_status"].pop(0) if calls["search_status"] else 200
        return httpx.Response(status, json=calls["search_body"])

    monkeypatch.setattr(kp.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return calls


def test_token_is_cached_across_searches(fake_kp):
    asyncio.run(kp.search({"query": "a"}))
    asyncio.run(kp.search({"query": "b"}))
    assert fake_kp["token"] == 1
    assert fake_kp["search"] == ["Bearer tok1", "Bearer tok1"]


def test_a_401_fetches_a_new_token_once(fake_kp):
    fake_kp["search_status"] = [401]
    assert asyncio.run(kp.search({"query": "a"})) == {"hits": []}
    assert fake_kp["search"] == ["Bearer tok1", "Bearer tok2"]


def test_a_second_401_is_an_error(fake_kp):
    fake_kp["search_status"] = [401, 401]
    with pytest.raises(kp.KnowledgeProviderError, match="401"):
        asyncio.run(kp.search({"query": "a"}))


def test_missing_credentials_is_an_error(fake_kp, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_PROVIDER_CLIENT_SECRET")
    with pytest.raises(kp.KnowledgeProviderError, match="credentials"):
        asyncio.run(kp.search({"query": "a"}))


def test_context_strings_match_the_old_tag_values():
    assert kp.context_strings({"status": "success", "has_day_range": True, "days_matched": False, "message": None}) == {
        "status": "success", "has_day_range": "true", "days_matched": "false",
    }


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="invalid JSON"),
    httpx.Response(200, json={}),
    httpx.Response(200, json={"access_token": ""}),
    httpx.Response(200, json={"access_token": "test-token", "expires_in": "invalid"}),
])
def test_malformed_token_response_does_not_reach_search(fake_kp, response):
    fake_kp["token_response"] = response
    with pytest.raises(kp.KnowledgeProviderError, match="invalid"):
        asyncio.run(kp.search({"query": "a"}))
    assert not fake_kp["search"]


@pytest.mark.parametrize("data", [
    {"hits": {"text": "a"}},
    {"hits": [], "scheme_search": []},
    {"hits": [], "advisory_search": "success"},
    {"hits": [], "effective_config": "default"},
])
def test_bad_search_response_shape_is_a_transport_failure(fake_kp, data):
    fake_kp["search_body"] = data
    with pytest.raises(kp.KnowledgeProviderError, match="invalid"):
        asyncio.run(kp.search({"query": "a"}))
