import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry

import agents.tools
from agents.tools import smam_scheme_status as v1
from agents.tools.v2 import provider_openapi
from agents.tools.v2 import smam_scheme_status as smam

OPERATION_REF = "https://specs.test/smam/openapi.yaml#/paths/~1api~1BeneficiaryService~1GetApplicationStatusByAI/post"
# The request half of SMAM's published contract: the one field it asks for.
OPENAPI_YAML = """
openapi: 3.1.1
info: {title: smam, version: 0.2.0}
paths:
  /api/BeneficiaryService/GetApplicationStatusByAI:
    post:
      operationId: readSmamBenefitStatus
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [SearchValue]
              properties:
                SearchValue: {type: string, minLength: 1, x-oan-from: subject.lookup}
      responses:
        '200': {description: ok}
"""

RESOURCE = {
    "id": "res-smam-benefits",
    "descriptor": {"code": "smam-benefits"},
    "resourceAttributes": {
        "@type": "openagrinet:SchemeApplication",
        "scheme": {"code": "SMAM", "name": "Sub-Mission on Agricultural Mechanization"},
        "actions": [{"action": "status", "operationRef": OPERATION_REF}],
    },
}
PROVIDER = {"id": "smam", "descriptor": {"code": "SMAM"}}
OFFER = {"id": "off:smam:benefits", "resourceIds": ["res-smam-benefits"]}
APPLICATIONS = [
    {"ApplicationID": 101, "ApplicationRefNo": "REF-1", "Implements": [{
        "ImplementSubsidyID": 11, "ImplementName": "Tractor",
        "StatusHistory": [
            {"StatusCode": 6, "StatusText": "Subsidy Approved", "StatusDate": "2018-07-29T01:01:31"},
            {"StatusCode": 1, "StatusText": "Waiting for Dealer Selection", "StatusDate": "2018-07-29T00:15:53"},
        ]}]},
    {"ApplicationID": 102, "ApplicationRefNo": "REF-2", "Implements": [{
        "ImplementSubsidyID": 12, "ImplementName": "Rotavator", "StatusHistory": []}]},
]


def _catalog(resources=(RESOURCE,), offers=(OFFER,)):
    return {"message": {"catalogs": [{"provider": PROVIDER, "resources": list(resources), "offers": list(offers)}]}}


def _on_status(answer):
    return {"message": {"contract": {"commitments": [{"commitmentAttributes": {
        "@type": "openagrinet:SchemeApplication", "scheme": {"code": "SMAM"}, **answer}}]}}}


class FakeAdapter:
    def __init__(self):
        self.discover = _catalog()
        self.status = _on_status({"success": True, "message": "Application status found successfully", "data": APPLICATIONS})
        self.openapi = OPENAPI_YAML
        self.bodies = {}

    def __call__(self, request):
        if request.method == "GET":
            return self._serve_openapi(request)
        action = request.url.path.rsplit("/", 1)[-1]
        self.bodies[action] = json.loads(request.content)
        answer = getattr(self, action)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def _serve_openapi(self, request):
        assert str(request.url) == OPERATION_REF.split("#")[0]
        if isinstance(self.openapi, Exception):
            raise self.openapi
        if isinstance(self.openapi, httpx.Response):
            return self.openapi
        return httpx.Response(200, text=self.openapi)


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("OAN_ADAPTER_URL", "http://adapter.test/consumer")
    monkeypatch.setenv("OAN_NETWORK_ID", "oan.test")
    monkeypatch.setattr(smam, "lf_update_current_observation", lambda **kwargs: None)
    monkeypatch.setattr(provider_openapi, "_OPENAPI_CACHE", {})
    fake = FakeAdapter()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs))
    return fake


CTX = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"))


def _check(search_type="mobile", value="99999 99999"):
    return asyncio.run(smam.check_smam_scheme_status(CTX, search_type, value))


def _v1_text(on_search):
    """What v1 showed the model for provider-backend's on_search, as the BAP passed it on."""
    return str(v1.SmamStatusApiResponse.model_validate({"responses": [{"context": {"action": "on_search"}, "message": on_search}]}))


def _tags(*pairs):
    return [{"descriptor": {"code": code, "name": name}, "value": value} for code, name, value in pairs]


V1_ON_SEARCH = {"catalog": {
    "descriptor": {"name": "SMAM Application Status", "code": "smam"},
    "tags": [{"descriptor": {"code": "search-context", "name": "Search Context"}, "list": _tags(
        ("status", "Status", "Success"), ("message", "Message", "Application status found successfully"),
        ("search-type", "Search Type", "application_no"), ("search-value", "Search Value", "9999999999"))}],
    "providers": [
        {"id": "101", "descriptor": {"name": "REF-1", "code": "101"}, "items": [{
            "id": "11", "descriptor": {"name": "Tractor", "code": "11"}, "tags": [
                {"descriptor": {"code": "implement-status", "name": "Implement Status"}, "list": _tags(
                    ("current-status-code", "Current Status Code", "6"),
                    ("current-status-text", "Current Status", "Subsidy Approved"),
                    ("current-status-date", "Status Date", "2018-07-29T01:01:31"))},
                {"descriptor": {"code": "status-history", "name": "Status History"}, "list": _tags(
                    ("history-1", "Step 1", json.dumps({"status_code": 6, "status_text": "Subsidy Approved", "status_date": "2018-07-29T01:01:31"})),
                    ("history-2", "Step 2", json.dumps({"status_code": 1, "status_text": "Waiting for Dealer Selection", "status_date": "2018-07-29T00:15:53"})))}]}]},
        {"id": "102", "descriptor": {"name": "REF-2", "code": "102"}, "items": [{
            "id": "12", "descriptor": {"name": "Rotavator", "code": "12"}, "tags": [
                {"descriptor": {"code": "implement-status", "name": "Implement Status"}, "list": _tags(
                    ("current-status-code", "Current Status Code", ""),
                    ("current-status-text", "Current Status", ""),
                    ("current-status-date", "Status Date", ""))},
                {"descriptor": {"code": "status-history", "name": "Status History"}, "list": []}]}]},
    ]}}


def test_discover_asks_the_public_catalog_for_smam_benefits(adapter):
    _check()
    body = adapter.bodies["discover"]
    assert body["context"]["networkId"] == "oan.test"
    assert '@.descriptor.code == "smam-benefits"' in body["message"]["intent"]["filters"]["expression"]


def test_status_sends_only_the_cleaned_search_value_against_the_published_offer(adapter):
    _check(value="+91 99999 99999")
    commitment = adapter.bodies["status"]["message"]["contract"]["commitments"][0]
    attributes = commitment["commitmentAttributes"]
    assert attributes["SearchValue"] == "9999999999"
    assert "searchType" not in attributes
    # The scheme the catalog published, not the tool's fallback: a resource that names itself wins.
    assert attributes["@type"] == "openagrinet:SchemeApplication"
    assert attributes["scheme"] == RESOURCE["resourceAttributes"]["scheme"]
    assert attributes["@context"].endswith("/SchemeApplication/v0.2/context.jsonld")
    assert commitment["offer"]["id"] == "off:smam:benefits" and commitment["offer"]["provider"] == PROVIDER


def test_applications_render_the_same_text_as_v1(adapter):
    assert _check() == _v1_text(V1_ON_SEARCH)


def test_data_sent_as_a_json_string_is_read_the_same(adapter):
    adapter.status = _on_status({"success": True, "message": "Application status found successfully", "data": json.dumps(APPLICATIONS)})
    assert _check() == _v1_text(V1_ON_SEARCH)


def test_no_application_returns_v1_failed_text(adapter):
    adapter.status = _on_status({"success": False, "message": "No application found", "data": None})
    assert _check() == "Status: Failed. No application found"


def test_no_application_without_a_message_uses_v1_default(adapter):
    adapter.status = _on_status({"success": False, "data": None})
    assert _check() == "Status: Failed. No applications found"


def test_adapter_error_returns_v1_unavailable_text(adapter):
    adapter.status = httpx.Response(500, json={"message": {"error": {"code": "x"}}})
    assert _check() == "SMAM application status service is unavailable. Please try again later."


def test_timeout_returns_v1_timeout_text(adapter):
    adapter.status = httpx.ReadTimeout("slow")
    assert _check() == "SMAM status request timed out. Please try again later."


def test_invalid_mobile_is_refused_without_a_call(adapter):
    out = _check(value="12345")
    assert out.startswith("Invalid mobile number")
    assert adapter.bodies == {}


def test_application_number_with_letters_is_sent_as_given(adapter):
    _check(search_type="application_no", value=" MH/2024/12 ")
    assert adapter.bodies["status"]["message"]["contract"]["commitments"][0]["commitmentAttributes"]["SearchValue"] == "MH/2024/12"


def test_resource_without_a_published_offer_is_not_used(adapter):
    adapter.discover = _catalog(offers=())
    assert _check() == "SMAM application status service is unavailable. Please try again later."
    assert "status" not in adapter.bodies


def test_nothing_discovered_returns_unavailable_without_status(adapter):
    adapter.discover = _catalog(resources=(), offers=())
    assert _check() == "SMAM application status service is unavailable. Please try again later."
    assert "status" not in adapter.bodies


def test_flag_swaps_in_the_v2_tool(monkeypatch):
    monkeypatch.setenv("SMAM_BECKN_VERSION", "v2")
    assert importlib.reload(agents.tools).check_smam_scheme_status is smam.check_smam_scheme_status
    monkeypatch.delenv("SMAM_BECKN_VERSION")
    assert importlib.reload(agents.tools).check_smam_scheme_status is v1.check_smam_scheme_status
