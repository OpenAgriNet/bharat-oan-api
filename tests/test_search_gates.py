"""Crop questions are searched straight away: no tool asks the farmer a question first."""

import asyncio
from types import SimpleNamespace

import pytest

from agents.tools import search as search_tools


class _FakeIndex:
    def search(self, **_):
        return {"hits": []}


class _FakeClient:
    def __init__(self, url=None):
        self.config = SimpleNamespace(timeout=None)

    def index(self, _name):
        return _FakeIndex()


@pytest.fixture
def marqo_stub(monkeypatch):
    monkeypatch.setenv("MARQO_ENDPOINT_URL", "http://marqo.test")
    monkeypatch.setenv("MARQO_INDEX_NAME", "idx")
    monkeypatch.setenv("MARQO_PESTS_DISEASES_INDEX_NAME", "pests-idx")
    monkeypatch.setattr(search_tools.marqo, "Client", _FakeClient)


# a brand-new conversation: only the farmer's question, the assistant has not said anything yet
FIRST_TURN_CTX = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"), messages=[])


def test_documents_search_runs_without_a_prior_question(marqo_stub):
    out = asyncio.run(search_tools.search_documents(FIRST_TURN_CTX, "aphids in wheat"))
    assert out.startswith("No ") and "aphids in wheat" in out


def test_pests_search_runs_once_the_crop_is_given(marqo_stub):
    out = asyncio.run(search_tools.search_pests_diseases(FIRST_TURN_CTX, "aphids", "Wheat"))
    assert out.startswith("No ") and "aphids" in out


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_pests_search_without_a_crop_asks_the_agent_to_ask_the_farmer(marqo_stub, blank):
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry, match="ask them which crop"):
        asyncio.run(search_tools.search_pests_diseases(FIRST_TURN_CTX, "aphids", blank))


def test_pests_tool_schema_requires_the_crop():
    from agents.tools import TOOLS

    tool = {t.name: t for t in TOOLS}["search_pests_diseases"]
    assert tool.function_schema.json_schema["required"] == ["query", "crop"]


def test_the_ask_first_helper_is_gone():
    with pytest.raises(ImportError):
        import helpers.ask_first  # noqa: F401
    assert not hasattr(search_tools, "farmer_was_asked")
