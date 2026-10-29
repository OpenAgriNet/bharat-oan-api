"""Scheme alias and live-catalog contracts, without models or external services."""
import importlib.util
import asyncio
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def schemes(monkeypatch):
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    stub("langfuse", observe=lambda **kwargs: lambda function: function)
    stub("helpers.langfuse_tracing", lf_update_current_observation=lambda **kwargs: None)
    stub("helpers.master_catalog", _tier_for_environment=lambda: "live",
         get_master_catalog_snapshot=lambda tier=None: {}, get_vector_scheme_entries=lambda: [])
    stub("qdrant_client", QdrantClient=object)
    stub("qdrant_client.models", FieldCondition=object, Filter=object, MatchValue=object)
    stub("sentence_transformers", SentenceTransformer=object)
    spec = importlib.util.spec_from_file_location("routing_under_test", ROOT / "helpers/scheme_qdrant_search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def catalog():
    # Sparse aliases reproduce the production catalog, rather than fixing the fixture.
    return [dict(scheme_code=code, scheme_name=name, scheme_aliases=[]) for code, name in [
        ("pk", "PM Kisan"), ("kcc", "Kisan Credit Card"), ("pmfby", "Pradhan Mantri Fasal Bima Yojana"),
        ("pksy", "PM Krishi Sinchayee Yojana"), ("pa", "PM-ASHA"),
        ("af", "AIF FAQ"), ("aea", "AIF Eligible Activities"),
        ("sathi", "SATHI"), ("smam", "Sub-Mission on Agricultural Mechanization"),
        ("pkvy-3", "Paramparagat Krishi Vikas Yojana"), ("nfsm", "National Food Security Mission"),
        ("rad", "Rainfed Area Development"), ("ffs-2", "Framework for Fertilizer Sales"),
        ("nbm", "National Bamboo Mission"), ("nbhm", "National Beekeeping & Honey Mission"),
    ]]


@pytest.mark.parametrize("query,code", [
    ("PMKISAN registration", "pk"), ("PM-KISAN farmer ID and land seeding process", "pk"),
    ("PM Kisan registration process", "pk"), ("P.M.K.I.S.A.N. eligibility", "pk"),
    ("Pradhan Mantri Kisan Samman Nidhi benefits", "pk"),
    ("Pradhanmantri Kisan Samman Nidhi benefits", "pk"), ("पीएम-किसान", "pk"),
    ("pk registration", "pk"), ("KCC benefits", "kcc"), ("PMFBY benefits", "pmfby"),
    ("P.M.K.S.Y. benefits", "pksy"), ("PMASHA benefits", "pa"), ("PM-ASHA benefits", "pa"),
    ("AIF benefits", "af"), ("Agriculture Infrastructure Fund benefits", "af"),
    ("AIF eligible activities", "aea"), ("Agriculture Infrastructure Fund eligible activities", "aea"),
    ("eligible activities under Agriculture Infrastructure Fund AIF", "aea"),
    ("Which activities eligible under AIF?", "aea"),
    ("SATHI benefits", "sathi"), ("SMAM benefits", "smam"), ("P.K.V.Y. benefits", "pkvy-3"),
    ("NFSM benefits", "nfsm"), ("RAD benefits", "rad"), ("FFS benefits", "ffs-2"),
    ("N.B.M. benefits", "nbm"), ("N.B.H.M. benefits", "nbhm"),
])
def test_resolves_public_names_before_network(schemes, catalog, query, code):
    assert schemes.resolve_scheme_code(query, catalog) == code
    assert not schemes.query_names_unindexed_scheme(query, catalog)


@pytest.mark.parametrize("query", ["PDMC benefits", "SHC benefits", "PMKISANX benefits",
    "nbh benefits", "Imaginary Lunar Tractor Grant eligibility", "farmyard manure benefits"])
def test_does_not_invent_catalog_coverage(schemes, catalog, query):
    assert schemes.resolve_scheme_code(query, catalog) is None
    assert schemes.query_names_unindexed_scheme(query, catalog)


def test_missing_catalog_does_not_publish_aliases_as_schemes(schemes):
    assert schemes.get_builtin_scheme_list() == []
    assert schemes.resolve_scheme_code("PM-KISAN benefits", []) is None


def test_aif_activities_do_not_route_to_an_unpublished_document(schemes, catalog):
    catalog = [item for item in catalog if item["scheme_code"] != "aea"]
    assert schemes.resolve_scheme_code("eligible activities under AIF", catalog) == "af"


def test_aliases_are_shared_with_prompt_and_follow_catalog_updates(schemes, monkeypatch):
    entries = [dict(code="pk", name="PM Kisan", aliases=["pk", "PM Kisan"])]
    monkeypatch.setattr(schemes, "get_vector_scheme_entries", lambda: entries)
    assert "PMKISAN" in schemes.get_builtin_scheme_list()[0]["scheme_aliases"]
    assert "PMKISAN" in schemes.format_vector_schemes_prompt_block()["vector_schemes_identifiers"]
    entries.clear()
    assert schemes.get_builtin_scheme_list() == []


def test_short_codes_do_not_match_inside_words(schemes, catalog):
    assert schemes.resolve_scheme_code("radish cultivation", catalog) is None
    assert schemes.resolve_scheme_code("paddy cultivation", catalog) is None


@pytest.fixture
def search_tool(schemes, catalog, monkeypatch):
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    stub("marqo")
    stub("agents.deps", FarmerContext=object)
    stub("app.config", DEFAULT_HTTP_TIMEOUT=10)
    stub("helpers.utils", get_logger=logging.getLogger)
    stub("agents.tools.terms", normalize_text_with_glossary=lambda text: text)
    monkeypatch.setitem(sys.modules, "helpers.scheme_qdrant_search", schemes)
    monkeypatch.setattr(schemes, "get_builtin_scheme_list", lambda: catalog)
    monkeypatch.setenv("BAP_ENDPOINT", "https://network.test")
    spec = importlib.util.spec_from_file_location("search_under_test", ROOT / "agents/tools/search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_search_sends_resolved_code_and_keeps_query(search_tool, monkeypatch):
    requests = []
    async def post(self, url, **kwargs):
        requests.append((url, kwargs["json"]))
        return httpx.Response(200, json={"responses": [{"message": {"catalog": {"providers": [{
            "items": [{"descriptor": {"code": "pk", "name": "PM Kisan", "long_desc": "Registration guidance."}}]
        }]}}}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test-session", question_id="test-question"))
    result = asyncio.run(search_tool.search_schemes(ctx, "PM-KISAN registration"))
    assert "Registration guidance." in result
    assert len(requests) == 1
    url, payload = requests[0]
    assert url == "https://network.test/search"
    assert payload["message"]["intent"]["category"]["descriptor"]["code"] == "scheme-agri-qdrant"
    assert payload["message"]["intent"]["item"]["descriptor"] == {"code":"pk", "name":"PM-KISAN registration"}
    assert payload["context"]["tags"] == {"session_id":"test-session", "question_id":"test-question"}


@pytest.mark.parametrize("failure,expected", [("timeout", "timed out"), ("http", "unavailable"), ("empty", "Could not find")])
def test_search_failure_does_not_retry_legacy_service(search_tool, monkeypatch, failure, expected):
    requests = []
    async def post(self, url, **kwargs):
        requests.append(kwargs["json"])
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout")
        return httpx.Response(503 if failure == "http" else 200, json={"responses": []})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test-session", question_id="test-question"))
    result = asyncio.run(search_tool.search_schemes(ctx, "PMKISAN registration"))
    assert expected.lower() in result.lower()
    assert len(requests) == 1
    assert requests[0]["message"]["intent"]["item"]["descriptor"]["code"] == "pk"
