"""The document tool uses KP for both profiles and cites only usable hits."""
import asyncio
import json

import httpx
import pytest

from agents.skill_catalog import get_catalog
from agents.skill_policy import SkillState
from tests.test_pmkisan_installment_release import search  # noqa: F401
from tests.test_production_reconciliation import load_module  # noqa: F401


@pytest.fixture
def call(search, monkeypatch):
    real_client = httpx.AsyncClient

    async def run(query, data, **args):
        requests = []

        def respond(request):
            requests.append(request)
            if isinstance(data, httpx.Response):
                return data
            return httpx.Response(200, json=data)

        monkeypatch.setattr(search.httpx, "AsyncClient", lambda: real_client(transport=httpx.MockTransport(respond)))
        result = await search.search_documents(query, **args)
        assert len(requests) == 1
        request = requests[0]
        assert str(request.url) == "https://example.test/search"
        assert request.headers["Authorization"] == "Bearer tok"
        return result, json.loads(request.content)

    return lambda *args, **kwargs: asyncio.run(run(*args, **kwargs))


def test_general_profile_keeps_document_citations_and_omits_videos(call):
    result, body = call("cooperative registration", {"hits": [
        {"name": "Cooperative handbook", "text": "Register with the state.", "section": "Registration",
         "page_start": 6, "page_end": 6},
        {"name": "Video title", "type": "video", "text": "Video result"},
    ]})
    assert body == {"query": "cooperative registration", "top_k": 10}
    assert "Source: Cooperative handbook | Registration | p. 6" in result
    assert "Register with the state." in result
    assert "Video" not in result


def test_scheme_profile_keeps_confirmed_code_and_status_offer(call):
    result, body = call("PM Kisan eligibility", {
        "scheme_search": {"status": "success", "resolved_scheme_code": "pk"},
        "hits": [
            {"scheme_code": "pk", "name": "PM Kisan", "text": "Eligibility details", "page_start": 2},
            {"scheme_code": "kcc", "name": "Kisan Credit Card", "text": "Unrelated scheme"},
        ],
    }, profile="scheme", scheme_code=" PK ", top_k=75)
    assert body == {
        "query": "PM Kisan eligibility", "document_kind": "scheme", "document_code": "pk", "top_k": 50,
    }
    assert "Eligibility details" in result and "PM-KISAN beneficiary" in result
    assert "Unrelated scheme" not in result


def test_scheme_search_discards_unrelated_hits(call):
    result, body = call("eNAM registration", {
        "scheme_search": {"status": "success", "resolved_scheme_code": "enam"},
        "hits": [{"scheme_code": "pk", "scheme_name": "PM Kisan", "text": "Other scheme details"}],
    }, profile="scheme")
    assert body == {"query": "eNAM registration", "document_kind": "scheme", "top_k": 10}
    assert "No scheme information found" in result
    assert "PM Kisan" not in result
    assert "Status check available" not in result


def test_a_transport_failure_is_distinct_from_no_matches(call):
    empty, _ = call("scheme", {"hits": []})
    failed, _ = call("scheme", httpx.Response(503))
    assert "No relevant documents found" in empty
    assert "service is unavailable" in failed


@pytest.mark.parametrize("language", get_catalog().manifest.agents["chat"].languages)
def test_active_scheme_and_advisory_workflows_advertise_only_current_tools(language):
    catalog = get_catalog()
    scheme = catalog.render_skill("schemes.information", language, {})
    assert 'search_documents(query="PM Kisan registration process", profile="scheme")' in scheme
    assert '`search_documents` with `profile="general"`' in scheme
    assert "video_url" not in scheme and "returns related videos" not in scheme
    visible = SkillState(enabled_skills=["schemes.information", "crop.advice"]).allowed_tools
    assert visible == {
        "search_documents", "search_advisory", "call_amul_vistaar_network", "call_maha_vistaar_network",
    }
    for skill in ("schemes.information", "crop.advice"):
        prompt = catalog.render_skill(skill, language, {})
        assert not any(name in prompt for name in ("search_schemes", "search_pests_diseases", "search_video"))
