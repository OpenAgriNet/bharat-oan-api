"""search_schemes is the one scheme tool: it routes MahaVistaar and Amul to their networks and adds eNAM videos."""
import asyncio
from types import SimpleNamespace

import pytest

from agents.tools import search

CTX = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"))


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def maha(ctx, scheme_code):
        seen.append(("maha", scheme_code))
        return "maha result"

    async def amul(ctx, query, union=None, provider_id=None):
        seen.append(("amul", query, union, provider_id))
        return "amul result"

    async def national(ctx, query, top_k):
        seen.append(("national", query, top_k))
        return "national result"

    async def videos(query, top_k=3):
        seen.append(("videos", query))
        return "\n\nvideos"

    monkeypatch.setattr(search, "call_maha_vistaar_network", maha)
    monkeypatch.setattr(search, "call_amul_vistaar_network", amul)
    monkeypatch.setattr(search, "_search_national_schemes", national)
    monkeypatch.setattr(search, "_enam_videos", videos)
    return seen


def test_national_is_the_default(calls):
    assert asyncio.run(search.search_schemes(CTX, "PM-KISAN eligibility")) == "national result"
    assert calls == [("national", "PM-KISAN eligibility", 10)]


def test_mahavistaar_goes_to_its_network_with_the_code(calls):
    out = asyncio.run(search.search_schemes(CTX, "drip irrigation", network="mahavistaar", scheme_code="aif"))
    assert out == "maha result" and calls == [("maha", "aif")]


def test_amul_goes_to_its_network_with_the_filters(calls):
    out = asyncio.run(search.search_schemes(CTX, "cattle insurance", network="amul", union="banas"))
    assert out == "amul result" and calls == [("amul", "cattle insurance", "banas", None)]


@pytest.mark.parametrize("query", ["eNAM registration", "e-NAM bids", "E NAM lots"])
def test_enam_questions_also_get_videos(calls, query):
    assert asyncio.run(search.search_schemes(CTX, query)) == "national result\n\nvideos"
    assert ("videos", query) in calls


def test_other_schemes_get_no_videos(calls):
    asyncio.run(search.search_schemes(CTX, "PMFBY claim"))
    assert not any(c[0] == "videos" for c in calls)


def test_other_documents_searches_kp_advisory_documents(monkeypatch):
    from agents.tools import advisory

    sent = []

    async def post(payload):
        sent.append(payload)
        return {"hits": [{"_id": "d1", "text": "State scheme text\n\nSource: State Agri Dept", "title_en": "State scheme"}],
                "advisory_search": {"status": "success"}}, ""

    monkeypatch.setattr(advisory, "_post_search", post)
    out = asyncio.run(search.search_schemes(CTX, "Rythu Bandhu scheme", network="other_documents"))
    assert sent == [{"query": "Rythu Bandhu scheme", "search_profile": "advisory"}]
    assert "State scheme text" in out and "Source: State Agri Dept" in out


def test_other_documents_with_nothing_found_says_so(monkeypatch):
    from agents.tools import advisory

    async def post(payload):
        return {"hits": [], "advisory_search": {"status": "success"}}, ""

    monkeypatch.setattr(advisory, "_post_search", post)
    assert asyncio.run(search.search_schemes(CTX, "unknown scheme", network="other_documents")) == search.NO_RESULTS_MESSAGE
