import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry

import agents.tools
from agents.tools import gfr as v1
from agents.tools.v2 import gfr as gfr
from agents.tools.v2 import provider_openapi

SPEC = "https://specs.test/api/external/gfr"
SOIL_REF = f"{SPEC}/soil-report/v0.2/openapi.yaml#/paths/~1/post"
ADVICE_REF = f"{SPEC}/advice/v0.2/openapi.yaml#/paths/~1/post"
SOIL_QUERY = "query GetTestForAuthUser($phone: PhoneNumber) { getTestForAuthUser(phone: $phone) { results } }"
ADVICE_QUERY = "query GetRecommendations($state: ID!) { getRecommendations(state: $state) }"
MP = "63f8e729dbc0f7fe0670d050"
KA = "63f99fbd519359b7438a84ca"
BARLEY = "6624d7a75dce468bfe6e1ed2"
LAT, LON = 22.085, 79.55


# The variables each capability declares, as the published contracts do: which
# held value fills each one, and the limit/skip/locale v1 hardcoded as defaults.
SOIL_VARIABLES = """
          required: [phone, cycle]
          properties:
            cycle: {type: string, x-oan-from: report.cycle}
            phone: {type: string, x-oan-from: subject.mobile}
            limit: {type: integer, default: 1}
            skip: {type: integer, default: 0}
            locale: {type: string, x-oan-from: subject.language, default: en}
"""
ADVICE_VARIABLES = """
          required: [state, results, crops, naturalFarming]
          properties:
            state: {type: string, x-oan-from: subject.state}
            results:
              type: object
              x-oan-from: soil.results
              properties:
                n: {}
                p: {}
                k: {}
                OC: {}
            crops: {type: array, items: {type: string}, x-oan-from: crop.ids}
            naturalFarming: {type: boolean, x-oan-from: farming.natural}
"""


def _openapi(schema_name, query, variables):
    return f"""
openapi: 3.1.1
paths:
  /:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/{schema_name}'
components:
  schemas:
    {schema_name}:
      type: object
      required: [query, variables]
      properties:
        query: {{const: '{query}'}}
        variables:
          type: object
{variables}
"""


OPENAPI = {
    SOIL_REF.split("#")[0]: _openapi("GfrSoilRequest", SOIL_QUERY, SOIL_VARIABLES),
    ADVICE_REF.split("#")[0]: _openapi("GfrAdviceRequest", ADVICE_QUERY, ADVICE_VARIABLES),
}


ADVISORY = "openagrinet:AgricultureAdvisory"
RESOURCE = "openagrinet:AgricultureResource"


def _state(shc_id=MP, lgd="23", name="MADHYA PRADESH", crops=((BARLEY, "Barley (Rainfed / Rabi)"),)):
    return {
        "id": f"res-gfr-advice:{lgd}",
        "descriptor": {"code": "gfr-advice"},
        "resourceAttributes": {
            "@type": ADVISORY,
            "agricultureSubjects": [{"descriptor": {"code": c, "name": n}} for c, n in crops],
            "coverageAreas": [
                {"codeScheme": "LGD", "areaLevel": "State", "areaCode": lgd, "areaName": name},
                {"codeScheme": "SHC", "areaLevel": "State", "areaCode": shc_id},
            ],
            "actions": [{"action": "select", "operationRef": ADVICE_REF}],
        },
    }


SOIL_RESOURCE = {
    "id": "res-gfr-soil-report",
    "descriptor": {"code": "gfr-soil-report"},
    "resourceAttributes": {"@type": RESOURCE, "actions": [{"action": "select", "operationRef": SOIL_REF}]},
}
# Left behind by an earlier publish of the same catalog; the tool must not use it.
STALE = {"id": "res-gfr:23", "descriptor": {"code": "gfr-advice"},
         "resourceAttributes": {"@type": RESOURCE, "actions": [{"action": "init", "operationRef": SOIL_REF}]}}


PROVIDER = {"id": "gfr", "descriptor": {"code": "GFR"}}
RECOMMENDATIONS = [{
    "crop": "Barley (Rainfed / Rabi)",
    "fertilizersdata": [{"name": "DAP", "values": "65.22", "unit": "Kg per Hectare"}],
    "fertilizersdatacombTwo": [{"name": "SSP", "values": "187.50", "unit": "Kg per Hectare"}],
    "organicFertilizer": {"fym": "4-5", "fymUnit": "Tonne per Hectare"},
}]


def _on_discover(*resources):
    return {"message": {"catalogs": [{"provider": PROVIDER, "resources": list(resources)}]}}


def _answer(attrs):
    return {"message": {"contract": {"commitments": [{"resources": [{"resourceAttributes": {"@type": "x", **attrs}}]}]}}}


def _selected_type(body):
    return body["message"]["contract"]["commitments"][0]["resources"][0]["resourceAttributes"]["@type"]


class FakeAdapter:
    def __init__(self):
        self.discover = _on_discover(STALE, _state(), SOIL_RESOURCE)
        self.soil = _answer({"results": {"n": 315, "p": 14, "k": 336, "OC": 0.9, "pH": 7.8}})
        self.advice = _answer({"data": {"getRecommendations": RECOMMENDATIONS}})
        self.bodies = {}

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, text=OPENAPI[str(request.url)])
        action = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        if action == "select":
            # The adapter picks the binding by the selected resource's @type.
            action = "soil" if _selected_type(body) == RESOURCE else "advice"
        self.bodies[action] = body
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
    monkeypatch.setattr(gfr, "lf_update_current_observation", lambda **kwargs: None)
    monkeypatch.setattr(provider_openapi, "_OPENAPI_CACHE", {})
    fake = FakeAdapter()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs))
    return fake


CTX = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"))


def _registry(**kwargs):
    return asyncio.run(gfr.gfr_get_crop_registries(CTX, LAT, LON, **kwargs))


def _recommend(**kwargs):
    args = {"state_id": MP, "crops": [BARLEY], "phone_no": "9999999999", "cycle": "2024-25", **kwargs}
    return asyncio.run(gfr.gfr_get_recommendations(CTX, **args))


def _v1_registry_item(code, name, state_id):
    return v1.GfrRegistryItem.model_validate({"id": code, "descriptor": {"name": name, "long_desc": name}, "tags": [
        {"descriptor": {"code": "crop_details"}, "list": [
            {"descriptor": {"code": "GFRavailable"}, "value": "Yes"},
            {"descriptor": {"code": "stateId"}, "value": state_id}]}]})


def test_registry_discovers_the_state_at_the_point(adapter):
    _registry()
    body = adapter.bodies["discover"]
    assert body["context"]["networkId"] == "oan.test"
    assert {c.split("#")[1] for c in body["context"]["schemaContext"]} == {RESOURCE, ADVISORY}
    assert '@.descriptor.code == "gfr-advice"' in body["message"]["intent"]["filters"]["expression"]
    assert body["message"]["intent"]["spatial"][0]["geometry"]["coordinates"] == [LON, LAT]


def test_registry_renders_the_same_lines_as_v1(adapter):
    expected = v1.format_crop_registry([_v1_registry_item(BARLEY, "Barley (Rainfed / Rabi)", MP)], True, None, 25)
    assert _registry() == expected
    assert f"- {BARLEY} - Barley (Rainfed / Rabi) - {MP} -" in expected


def test_registry_name_filter_and_limit_follow_v1(adapter):
    adapter.discover = _on_discover(SOIL_RESOURCE, _state(crops=((BARLEY, "Barley (Rainfed / Rabi)"), ("w1", "Wheat (Irrigated / Rabi)"), ("w2", "Wheat (Rainfed / Rabi)"))))
    assert "Barley" not in _registry(crop_name_contains="wheat")
    assert _registry(limit=1).count("\n- ") == 1


def test_registry_lists_both_states_at_a_border_point(adapter):
    adapter.discover = _on_discover(_state(), _state(shc_id=KA, lgd="29", name="KARNATAKA", crops=(("ka1", "Ragi (Rainfed / Kharif)"),)))
    out = _registry()
    assert f"- {BARLEY} - Barley (Rainfed / Rabi) - {MP} -" in out
    assert f"- ka1 - Ragi (Rainfed / Kharif) - {KA} -" in out


def test_registry_with_no_state_found_says_no_crops(adapter):
    adapter.discover = _on_discover(SOIL_RESOURCE, STALE)
    assert _registry() == "No crops found."


def test_recommendation_discovers_the_state_by_its_shc_id_and_the_soil_report(adapter):
    _recommend()
    expression = adapter.bodies["discover"]["message"]["intent"]["filters"]["expression"]
    assert '@.descriptor.code == "gfr-advice"' in expression and f'areaCode == "{MP}"' in expression
    assert '@.descriptor.code == "gfr-soil-report"' in expression


def test_soil_select_sends_the_soil_query_with_v1_variables(adapter):
    _recommend(phone_no="+91 99999 99999")
    commitment = adapter.bodies["soil"]["message"]["contract"]["commitments"][0]
    sent = commitment["resources"][0]["resourceAttributes"]
    assert sent["@type"] == RESOURCE and sent["query"] == SOIL_QUERY
    assert sent["variables"] == {"phone": "+919999999999", "cycle": "2024-25", "limit": 1, "skip": 0, "locale": "en"}
    assert commitment["offer"]["provider"] == PROVIDER and commitment["resources"][0]["id"] == "res-gfr-soil-report"


def test_select_sends_only_n_p_k_oc_from_the_soil_test(adapter):
    _recommend(natural_farming=True)
    resource = adapter.bodies["advice"]["message"]["contract"]["commitments"][0]["resources"][0]
    sent = resource["resourceAttributes"]
    assert resource["id"] == "res-gfr-advice:23" and sent["@type"] == ADVISORY and sent["query"] == ADVICE_QUERY
    assert sent["variables"] == {"state": MP, "results": {"n": 315, "p": 14, "k": 336, "OC": 0.9}, "crops": [BARLEY], "naturalFarming": True}
    assert adapter.bodies["advice"]["context"]["transactionId"] == adapter.bodies["soil"]["context"]["transactionId"]


def test_recommendation_renders_the_same_text_as_v1(adapter):
    item = {"id": "gfr-recommendation", "descriptor": {"name": "Crop Recommendation"},
            "tags": [{"descriptor": {"code": "recommendations"}, "list": [{"descriptor": {"code": "data"}, "value": json.dumps(RECOMMENDATIONS)}]}]}
    assert _recommend() == v1.format_recommendations([item])


def test_no_soil_test_returns_v1_no_data_text_without_advice(adapter):
    adapter.soil = _answer({})
    assert _recommend() == "No recommendation data found."
    assert "advice" not in adapter.bodies


def test_shc_refusal_returns_v1_no_data_text(adapter):
    adapter.soil = _answer({"errors": [{"message": "Not Authorized"}]})
    assert _recommend() == "No recommendation data found."


def test_unknown_state_returns_v1_no_data_text_without_a_select(adapter):
    adapter.discover = _on_discover(SOIL_RESOURCE, STALE)
    assert _recommend() == "No recommendation data found."
    assert "soil" not in adapter.bodies


def test_missing_soil_report_resource_returns_v1_no_data_text(adapter):
    adapter.discover = _on_discover(_state())
    assert _recommend() == "No recommendation data found."
    assert "soil" not in adapter.bodies


def test_adapter_error_asks_the_model_to_retry_with_v1_text(adapter):
    adapter.soil = httpx.Response(500, json={"error": "x"})
    with pytest.raises(ModelRetry, match="GFR service unavailable right now"):
        _recommend()


def test_timeout_asks_the_model_to_retry_with_v1_text(adapter):
    adapter.advice = httpx.ReadTimeout("slow")
    with pytest.raises(ModelRetry, match="GFR network request timed out"):
        _recommend()


def test_bad_phone_and_state_are_refused_without_a_call(adapter):
    with pytest.raises(ModelRetry, match="valid Indian mobile number"):
        _recommend(phone_no="123")
    with pytest.raises(ModelRetry, match="valid state ID"):
        _recommend(state_id="x")
    assert adapter.bodies == {}


def test_flag_swaps_in_the_v2_tools(monkeypatch):
    monkeypatch.setenv("GFR_BECKN_VERSION", "v2")
    tools = importlib.reload(agents.tools)
    assert tools.gfr_get_crop_registries is gfr.gfr_get_crop_registries
    assert tools.gfr_get_recommendations is gfr.gfr_get_recommendations
    monkeypatch.delenv("GFR_BECKN_VERSION")
    assert importlib.reload(agents.tools).gfr_get_crop_registries is v1.gfr_get_crop_registries
