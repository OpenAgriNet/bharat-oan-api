import asyncio
import base64
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry

import agents.tools
from agents.tools import shc_scheme_status as v1
from agents.tools.v2 import provider_openapi
from agents.tools.v2 import shc_scheme_status as shc

OPERATION_REF = (
    "https://raw.githubusercontent.com/OpenAgriNet/network-specs/bv-features/api/external/shc/reports/v0.2/openapi.yaml"
    "#/paths/~1/post"
)
QUERY = "query GetTestForAuthUser($phone: PhoneNumber, $cycle: String) { getTestForAuthUser(phone: $phone) { id } }"
OPENAPI_YAML = f"""
openapi: 3.1.1
paths:
  /:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ShcReportRequest'
components:
  schemas:
    ShcReportRequest:
      type: object
      required: [query, variables]
      properties:
        query: {{const: '{QUERY}'}}
        # The variables SHC's published contract declares: which held value fills
        # each one, and the page size and locale v1 hardcoded as defaults.
        variables:
          type: object
          required: [phone, cycle]
          properties:
            phone: {{type: string, x-oan-from: subject.mobile}}
            cycle: {{type: string, x-oan-from: report.cycle}}
            locale: {{type: string, x-oan-from: subject.language, default: en}}
            limit: {{type: integer, default: 10}}
            skip: {{type: integer, default: 0}}
"""
PROVIDER = {"id": "shc", "descriptor": {"code": "SHC", "name": "Soil Health Card"}}
RESOURCE = {
    "id": "res-shc-reports",
    "descriptor": {"code": "shc-reports"},
    "resourceAttributes": {
        "@type": "openagrinet:AgricultureResource",
        "actions": [{"action": "select", "operationRef": OPERATION_REF}],
    },
}
HTML = "<html><body><div class='container'><h1>Soil Report</h1><p>pH: 7.1</p></div></body></html>"


def _report(report_id="R1", farmer="Test Farmer", **overrides):
    report = {
        "id": report_id,
        "computedID": f"C-{report_id}",
        "farmer": {"name": farmer},
        "scheme": {"_id": "S1", "department": "Test Department"},
        "reportData": {"parameterInfos": []},
        "rdfValues": {"fertilizerRecommendation_details": [], "deficiency": []},
        "html": HTML,
    }
    report.update(overrides)
    return report


def _on_select(reports=None, errors=None):
    answer = {"@type": "openagrinet:AgricultureResource", "data": {"getTestForAuthUser": reports}}
    if errors:
        answer["errors"] = errors
    return {"message": {"contract": {"commitments": [{"resources": [{"resourceAttributes": answer}]}]}}}


def _on_discover(*resources):
    return {"message": {"catalogs": [{"provider": PROVIDER, "resources": list(resources)}]}}


class FakeAdapter:
    def __init__(self):
        self.discover = _on_discover(RESOURCE)
        self.select = _on_select([_report()])
        self.bodies = {}

    def __call__(self, request):
        if request.method == "GET":
            assert str(request.url) == OPERATION_REF.split("#")[0]
            return httpx.Response(200, text=OPENAPI_YAML)
        action = request.url.path.rsplit("/", 1)[-1]
        self.bodies[action] = json.loads(request.content)
        answer = getattr(self, action)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("OAN_ADAPTER_URL", "http://adapter.test/consumer")
    monkeypatch.setenv("OAN_NETWORK_ID", "oan.test")
    monkeypatch.delenv("OAN_PUBLIC_NETWORK_ID", raising=False)
    monkeypatch.setattr(shc, "lf_update_current_observation", lambda **kwargs: None)
    monkeypatch.setattr(provider_openapi, "_OPENAPI_CACHE", {})
    monkeypatch.setattr(v1, "generate_file_hash", lambda phone, cycle: "fixedhash")
    monkeypatch.setattr(v1, "API_BASE_URL", "http://api.test")

    async def no_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(v1.cache, "set", no_cache)
    fake = FakeAdapter()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs),
    )
    return fake


def _check(phone="9999999999", cycle="2024-25"):
    return asyncio.run(shc.check_shc_status(SimpleNamespace(deps=SimpleNamespace()), phone, cycle))


def _v1_render(reports):
    """What v1 showed the model for the reply provider-backend built from these reports."""
    items = [
        v1.Item(
            id=r["computedID"], tags=[],
            descriptor=v1.Descriptor(name=f"Soil Health Card for Farmer {r['farmer']['name']}"),
            media=[v1.MediaResource(mimetype="text/html", url=base64.b64encode(r["html"].encode()).decode())],
        )
        for r in reports
    ]
    provider = v1.Provider(
        id="S1", items=items,
        descriptor=v1.Descriptor(name="Test Department", short_desc="Govt Soil Testing Lab - Maharashtra, India"),
    )
    for i, item in enumerate(items):
        item.media[0].ensure_injected("9999999999", "2024-25", f"0-0-{i}-0")
        asyncio.run(item.media[0].cache_and_update_url())
    return ("\n" + str(provider)).strip()


def test_discover_asks_the_public_catalog_for_shc_reports(adapter):
    _check()
    body = adapter.bodies["discover"]
    assert body["context"]["networkId"] == "*"
    assert '@.descriptor.code == "shc-reports"' in body["message"]["intent"]["filters"]["expression"]
    assert "spatial" not in body["message"]["intent"]


def test_select_sends_the_openapi_query_and_v1_variables(adapter):
    _check(phone="99999 99999")
    commitment = adapter.bodies["select"]["message"]["contract"]["commitments"][0]
    sent = commitment["resources"][0]["resourceAttributes"]
    assert sent["@type"] == "openagrinet:AgricultureResource"
    assert sent["query"] == QUERY
    assert sent["variables"] == {"phone": "+919999999999", "cycle": "2024-25", "limit": 10, "skip": 0, "locale": "en"}
    assert commitment["resources"][0]["id"] == "res-shc-reports"
    assert commitment["offer"]["provider"] == PROVIDER


def test_reports_render_the_same_text_as_v1(adapter):
    reports = [_report("R1", "Farmer One"), _report("R2", "Farmer Two")]
    adapter.select = _on_select(reports)
    assert _check() == _v1_render(reports)


def test_report_missing_v1_required_fields_is_dropped(adapter):
    adapter.select = _on_select([_report("R1", "Kept"), _report("R2", "Dropped", rdfValues={"deficiency": []})])
    out = _check()
    assert "Kept" in out and "Dropped" not in out


def test_no_reports_returns_no_data_available(adapter):
    adapter.select = _on_select([])
    assert _check() == "No data available."


def test_all_reports_dropped_returns_no_data_available(adapter):
    adapter.select = _on_select([_report(reportData=None)])
    assert _check() == "No data available."


def test_graphql_error_returns_service_unavailable(adapter):
    adapter.select = _on_select(None, errors=[{"message": "Not Authorized"}])
    assert _check() == shc._UNAVAILABLE


def test_no_shc_resource_in_discover_returns_service_unavailable_without_select(adapter):
    adapter.discover = _on_discover()
    assert _check() == shc._UNAVAILABLE
    assert "select" not in adapter.bodies


def test_adapter_error_returns_v1_status_code_text(adapter):
    adapter.select = httpx.Response(500, json={"error": "x"})
    assert _check() == "Soil health card status service unavailable. Status code: 500"


def test_timeout_returns_v1_timeout_text(adapter):
    adapter.select = httpx.ReadTimeout("slow")
    assert _check() == "Soil health card status request timed out. Please try again later."


def test_invalid_phone_asks_the_model_to_retry_without_a_call(adapter):
    with pytest.raises(ModelRetry):
        _check(phone="12345")
    assert adapter.bodies == {}


def test_flag_swaps_in_the_v2_tool(monkeypatch):
    monkeypatch.setenv("SHC_BECKN_VERSION", "v2")
    assert importlib.reload(agents.tools).check_shc_status is shc.check_shc_status
    monkeypatch.delenv("SHC_BECKN_VERSION")
    assert importlib.reload(agents.tools).check_shc_status is v1.check_shc_status
