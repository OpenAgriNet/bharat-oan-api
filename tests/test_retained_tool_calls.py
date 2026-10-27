"""Exercise retained tool entry points with isolated HTTP, search and tracing."""
import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import dotenv
import httpx
import pytest
from pydantic_ai import ModelRetry, Tool

from tests.test_production_reconciliation import load_module


@pytest.fixture
def offline_module(load_module, monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys.modules["helpers.utils"], "get_today_date_str",
                        lambda: "27 October 2026", raising=False)
    return load_module


@pytest.fixture
def maps(offline_module, monkeypatch):
    monkeypatch.setenv("PHOTON_HOST", "http://photon.test:2322")
    client = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    module = offline_module("agents/tools/maps.py")
    return module, client


@pytest.mark.parametrize("kind", ["success", "empty", "bad_request", "server", "timeout", "connection"])
def test_forward_geocoding_without_removed_session_cache(maps, kind):
    module, client = maps
    url = "http://photon.test:2322/api"
    request = httpx.Request("GET", url)
    if kind in {"timeout", "connection"}:
        client.get.side_effect = httpx.ReadTimeout("test") if kind == "timeout" else httpx.ConnectError("test")
    else:
        status = 400 if kind == "bad_request" else 500 if kind == "server" else 200
        features = [] if kind == "empty" else [{
            "geometry": {"coordinates": [73.85, 18.52]},
            "properties": {"name": "Pune", "state": "Maharashtra", "country": "India", "type": "city"},
        }]
        client.get.return_value = httpx.Response(status, json={"features": features}, request=request)
    result = asyncio.run(module.forward_geocode("Pune"))
    assert client.get.await_count == 1
    if kind == "success":
        assert result == "Pune, Maharashtra, India (Latitude: 18.52, Longitude: 73.85)"
    else:
        assert "Please" in result or "timed out" in result or "Could not connect" in result
    assert set(Tool(module.forward_geocode, takes_ctx=False).function_schema.json_schema["properties"]) == {
        "place_name"
    }


@pytest.mark.parametrize("kind", ["success", "missing_config", "http_error", "timeout"])
def test_weather_uses_only_existing_forecast_service(offline_module, monkeypatch, kind):
    module = offline_module("agents/tools/weather.py")
    monkeypatch.setenv("BAP_ENDPOINT", "https://bap.test")
    if kind == "missing_config":
        monkeypatch.delenv("BAP_ENDPOINT")
    calls = []

    def post(url, *, json, timeout):
        calls.append((url, json))
        if kind == "timeout":
            raise httpx.ReadTimeout("test")
        return httpx.Response(500 if kind == "http_error" else 200, json={})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(module.WeatherResponse, "model_validate", lambda data: "Forecast from provider")
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test-session", question_id="test-question"))
    result = asyncio.run(module.weather_forecast(ctx, 18.52, 73.85))
    assert len(calls) == (0 if kind == "missing_config" else 1)
    if kind == "success":
        assert result == "Forecast from provider"
        assert calls[0][0] == "https://bap.test/search"
        assert calls[0][1]["context"]["tags"] == {
            "session_id": "test-session", "question_id": "test-question"
        }
    else:
        assert any(word in result for word in ("configuration error", "unavailable", "timed out"))


@pytest.fixture
def search(offline_module, monkeypatch):
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    result = {"hits": []}
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        return result

    client = SimpleNamespace(config=SimpleNamespace(timeout=None),
                             index=lambda name: SimpleNamespace(search=query))
    stub("marqo", Client=lambda **kwargs: client)
    stub("agents.tools.terms", normalize_text_with_glossary=lambda text: text)
    stub(
        "helpers.scheme_qdrant_search",
        format_qdrant_scheme_codes_for_doc=lambda: "",
        format_scheme_unavailable=lambda *args: "",
        format_search_results=lambda *args: "",
        format_status_check_offer=lambda *args: "",
        get_builtin_scheme_list=lambda: [],
        query_names_unindexed_scheme=lambda *args: None,
        resolve_scheme_code=lambda *args: None,
    )
    monkeypatch.setenv("MARQO_ENDPOINT_URL", "http://marqo.test")
    monkeypatch.setenv("MARQO_PESTS_DISEASES_INDEX_NAME", "pests")
    return offline_module("agents/tools/search.py"), result, calls


@pytest.mark.parametrize("name", ["search_documents", "search_pests_diseases"])
@pytest.mark.parametrize("kind", ["success", "empty", "config"])
def test_crop_search_does_not_offer_or_fetch_removed_advisories(search, monkeypatch, name, kind):
    module, result, calls = search
    if kind == "success":
        result["hits"] = [{
            "name": "Wheat advisory", "text": "Verified crop guidance", "doc_id": "test-doc",
            "type": "document", "source": "Test institution", "_score": 1.0, "_id": "test-hit",
        }]
    if kind == "config":
        monkeypatch.delenv("MARQO_ENDPOINT_URL")
        with pytest.raises(ModelRetry):
            asyncio.run(getattr(module, name)("wheat"))
        assert not calls
    else:
        output = asyncio.run(getattr(module, name)("wheat"))
        assert len(calls) == 1
        assert calls[0]["q"] == "wheat"
        assert ("Verified crop guidance" in output) == (kind == "success")
        assert "agromet" not in output.lower()
    tool = Tool(getattr(module, name), takes_ctx=False)
    assert set(tool.function_schema.json_schema["properties"]) == {"query", "top_k"}
