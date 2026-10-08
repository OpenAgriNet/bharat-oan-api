"""Exercise production contracts without starting model clients or external services."""
import importlib.util
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def load_module(monkeypatch):
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    stub("helpers.utils", get_logger=logging.getLogger, to_ascii_digits=lambda text: str(text).translate(str.maketrans("०१२३४५६७८९", "0123456789")))
    stub("app.config", DEFAULT_HTTP_TIMEOUT=10)
    stub("agents.deps", FarmerContext=object)
    stub("langfuse", observe=lambda **kwargs: lambda function: function)
    stub("helpers.langfuse_tracing", lf_update_current_observation=lambda **kwargs: None)

    def load(path):
        name = "reconciliation_" + Path(path).stem
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    return load


@pytest.fixture
def pmfby(load_module, monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "https://example.test")
    module = load_module("agents/tools/pmfby_grievance.py")
    module.ctx = SimpleNamespace(deps=SimpleNamespace(session_id="session-1", question_id="question-1"))
    return module


@pytest.mark.parametrize("phone", ["123456", "123", "letters", ""])
def test_otp_initiation_rejects_invalid_mobile_before_request(pmfby, monkeypatch, phone):
    monkeypatch.setattr(pmfby, "_post_json_logged", lambda *args: pytest.fail("invalid mobile reached BAP"))
    result = pmfby.initiate_pmfby_grievance_otp(pmfby.ctx, phone)
    assert "OTP has been sent" not in result
    assert "mobile" in result.lower()


def test_empty_http_200_does_not_verify_otp(pmfby, monkeypatch):
    monkeypatch.setattr(pmfby, "_post_json_logged", lambda *args: httpx.Response(200, json={"responses": []}))
    result = pmfby.check_pmfby_grievance_otp(pmfby.ctx, "123456", "9876543210")
    assert result.startswith("OTP verification failed")


def test_explicit_invalid_otp_overrides_success_state(pmfby):
    response = {"responses": [{"message": {"order": {"state": "COMPLETED", "items": [{"id": "policy"}], "tags": [{"descriptor": {"code": "invalid_otp"}, "value": "invalid otp"}]}}}]}
    assert not pmfby._pmfby_otp_status_verified(response)
    assert pmfby._bap_status_error_message(response)


def test_verified_policy_request_keeps_tracing_and_parameters(pmfby, monkeypatch):
    captured = []
    def post(url, payload, label):
        captured.append((url, payload))
        return httpx.Response(200, json={"responses": [{"message": {"order": {"state": "COMPLETED", "items": [{"id": "policy"}]}}}]})
    monkeypatch.setattr(pmfby, "_post_json_logged", post)
    result = pmfby.check_pmfby_grievance_otp(pmfby.ctx, "123456", "+91 9876543210", year="2026", season="Rabi")
    assert result.startswith("OTP verified")
    url, payload = captured[0]
    assert url.endswith("/status")
    assert payload["context"]["tags"] == {"session_id": "session-1", "question_id": "question-1"}
    customer = payload["message"]["order"]["fulfillments"][0]["customer"]
    assert customer["contact"]["phone"] == "9876543210"
    assert {tag["descriptor"]["code"]: tag["value"] for tag in customer["person"]["tags"]} == {"inquiry_type": "policy_status", "year": "2026", "season": "Rabi"}


def test_submit_does_not_run_after_failed_otp_verification(pmfby, monkeypatch):
    calls = []
    def post(url, payload, label):
        calls.append(url)
        return httpx.Response(200, json={"responses": []})
    monkeypatch.setattr(pmfby, "_post_json_logged", post)
    result = pmfby.pmfby_submit_grievance(pmfby.ctx, "123456", "9876543210", "2026", "Kharif", "application", "Crop insurance payment is pending")
    assert result.startswith("OTP verification failed")
    assert calls == ["https://example.test/status"]


def test_grievance_status_search_parses_catalog(pmfby, monkeypatch):
    calls = []
    def post(url, payload, label):
        calls.append((url, payload))
        return httpx.Response(200, json={"responses": [{"message": {"catalog": {"providers": [{"items": [{"tags": [{"display": True, "descriptor": {"code": "grievance_status_fetched"}, "list": [{"display": True, "descriptor": {"code": "status", "name": "Status"}, "value": "Pending"}]}]}]}]}}}]})
    monkeypatch.setattr(pmfby, "_post_json_logged", post)
    assert "Status: Pending" in pmfby.pmfby_grievance_status(pmfby.ctx, "9876543210", "123456789012")
    url, payload = calls[0]
    assert url.endswith("/search")
    assert payload["context"]["action"] == "search"
    assert payload["context"]["tags"]["question_id"] == "question-1"


@pytest.mark.parametrize("configured", [False, True])
def test_mahavistaar_never_targets_bpp(load_module, monkeypatch, configured):
    if configured:
        monkeypatch.setenv("MH_BPP_ID", "provider-that-must-not-be-sent")
    else:
        monkeypatch.delenv("MH_BPP_ID", raising=False)
    module = load_module("agents/tools/maha_vistaar.py")
    for code in ["ndksp-drip-irrigation", "ndksp-farm-pond-lining", "aif"]:
        assert "bpp_id" not in module.build_scheme_search_payload(code)["context"]


def test_photon_preserves_configured_https_endpoint(load_module, monkeypatch):
    monkeypatch.setenv("PHOTON_HOST", "https://example.test:443/photon/")
    module = load_module("app/core/npss_geocodes.py")
    assert module.PHOTON_BASE_URL == "https://example.test:443/photon"
    assert module._validated_photon_base_url("http://example.test") is None
    assert module._validated_photon_base_url(None) is None


@pytest.mark.parametrize("timestamp", ["2020-01-01T00:00:00", "2020-01-01T00:00:00+00:00"])
def test_image_cleanup_accepts_naive_and_aware_metadata(load_module, monkeypatch, tmp_path, timestamp):
    monkeypatch.setenv("TEMP_UPLOAD_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_MOUNT_DIR", raising=False)
    module = load_module("app/core/image_storage.py")
    image_id = str(uuid.uuid4())
    path = module.TEMP_UPLOAD_DIR / (image_id + ".jpg")
    path.write_bytes(b"image")
    metadata = module._deserialize_metadata({"path": str(path), "created_at": timestamp, "processed": True})
    assert metadata["created_at"].tzinfo == timezone.utc
    assert metadata["created_at"] < datetime.now(timezone.utc)
    module._persist_metadata(image_id, metadata)
    assert module.cleanup_expired_images() == 1
    assert not path.exists()


@pytest.mark.parametrize("supplied_qid", [None, "frontend-question-id"])
def test_chat_returns_actual_stream_qid_in_exposed_header(load_module, monkeypatch, supplied_qid):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    async def history(session_id):
        return session_id, []

    captured = []
    async def stream(**kwargs):
        captured.append(kwargs)
        yield "answer"

    stub("app.auth.jwt_auth", get_current_user=lambda: {"channel": "BharatVistaar"})
    models = load_module("app/models/requests.py")
    stub("app.models.requests", ChatRequest=models.ChatRequest)
    stub("app.routers.chat_query_utils", get_session_history=history, normalize_chat_query=lambda query: (query, False), prepare_image_analyze_payload=lambda **kwargs: None)
    stub("app.services.chat", stream_chat_messages=stream)
    router = load_module("app/routers/chat.py")
    app = FastAPI()
    app.include_router(router.router)
    params = {"query": "hello", "session_id": "session-1"}
    if supplied_qid:
        params["qid"] = supplied_qid
    response = TestClient(app).get("/chat/", params=params)
    assert response.status_code == 200
    assert response.text == "answer"
    returned_qid = response.headers["x-qid"]
    assert response.headers["access-control-expose-headers"] == "X-QID"
    assert returned_qid == captured[0]["qid"]
    if supplied_qid:
        assert returned_qid == supplied_qid
    else:
        uuid.UUID(returned_qid)
