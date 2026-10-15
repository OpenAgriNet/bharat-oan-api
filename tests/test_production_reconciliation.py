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
@pytest.mark.parametrize("coordinates", [None, ("", ""), ("28.6", "77.2")])
def test_chat_returns_actual_stream_qid_in_exposed_header(load_module, monkeypatch, supplied_qid, coordinates):
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
    if coordinates is not None:
        params.update(latitude=coordinates[0], longitude=coordinates[1])
    response = TestClient(app).get("/chat/", params=params)
    assert response.status_code == 200
    assert response.text == "answer"
    returned_qid = response.headers["x-qid"]
    assert response.headers["access-control-expose-headers"] == "X-QID"
    assert returned_qid == captured[0]["qid"]
    assert captured[0]["latitude"] == (28.6 if coordinates and coordinates[0] else None)
    assert captured[0]["longitude"] == (77.2 if coordinates and coordinates[1] else None)
    if supplied_qid:
        assert returned_qid == supplied_qid
    else:
        uuid.UUID(returned_qid)


@pytest.fixture
def token_module(load_module, monkeypatch, tmp_path):
    settings = SimpleNamespace(jwt_private_key_path=None, base_dir=tmp_path,
        jwt_algorithm="RS256", jwt_expiry_minutes=15,
        play_integrity_package_name_prefix="PLAY_INTEGRITY_PACKAGE_NAME_",
        play_integrity_private_key_prefix="PLAY_INTEGRITY_PRIVATE_KEY_",
        api_key_auth_token_prefix="API_KEY_AUTH_TOKEN_")
    config = ModuleType("app.config")
    config.settings = settings
    config.get_default_httpx_timeout = lambda: 10
    config.DEFAULT_HTTP_TIMEOUT = 10
    monkeypatch.setitem(sys.modules, "app.config", config)
    cache_module = ModuleType("app.core.cache")
    cache_module.cache = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "app.core.cache", cache_module)
    return load_module("app/routers/token.py")


@pytest.mark.parametrize("kind", ["package", "api_key"])
@pytest.mark.parametrize("value", ["  configured-value  ", "  "])
def test_auth_configuration_trims_values_and_rejects_whitespace(token_module, monkeypatch, kind, value):
    from fastapi import HTTPException
    key = "PLAY_INTEGRITY_PACKAGE_NAME_TEST_CLIENT" if kind == "package" else "API_KEY_AUTH_TOKEN_TEST_CLIENT"
    resolve = token_module._resolve_play_integrity_package_name if kind == "package" else token_module._resolve_api_key
    monkeypatch.setenv(key, value)
    if value.strip():
        assert resolve("test-client") == "configured-value"
    else:
        with pytest.raises(HTTPException) as error:
            resolve("test-client")
        assert error.value.status_code == 500


def test_play_integrity_normalizes_escaped_private_key(token_module, monkeypatch, tmp_path):
    import asyncio
    import json
    service_account_path = tmp_path / "service-account.json"
    service_account_path.write_text(json.dumps({"private_key": "placeholder"}))
    monkeypatch.setattr(token_module, "_resolve_service_account_path", lambda client: service_account_path)
    monkeypatch.setenv("PLAY_INTEGRITY_PRIVATE_KEY_TEST_CLIENT", "BEGIN\\nEND")
    captured = []
    def credentials(info, scopes):
        captured.append(info)
        return SimpleNamespace(valid=True, token="test-access-token")
    monkeypatch.setattr(token_module.service_account.Credentials, "from_service_account_info", credentials)
    assert asyncio.run(token_module._get_play_integrity_access_token("test-client")) == "test-access-token"
    assert captured[0]["private_key"] == "BEGIN\nEND"


def test_guest_cannot_choose_admin_role(token_module, monkeypatch):
    import asyncio
    captured = []
    monkeypatch.setattr(token_module, "private_key", object())
    monkeypatch.setattr(token_module.jwt, "encode", lambda payload, key, algorithm: captured.append(payload) or "test-token")
    request = token_module.AuthRequest.model_validate({"role": "admin", "fingerprint_id": "device-1"})
    asyncio.run(token_module.create_auth_token(request))
    assert captured[0]["role"] == "public"
    assert captured[0]["sub"] == "guest:device-1"


def test_api_key_cannot_override_jwt_claims(token_module, monkeypatch):
    import asyncio
    captured = []
    monkeypatch.setenv("API_KEY_AUTH_TOKEN_TEST_CLIENT", "test-api-key")
    monkeypatch.setattr(token_module, "private_key", object())
    monkeypatch.setattr(token_module.jwt, "encode", lambda payload, key, algorithm: captured.append(payload) or "test-token")
    request = token_module.ApiKeyAuthRequest.model_validate({"client_code": "test-client", "claims": {"role": "admin", "channel": "other-client"}})
    asyncio.run(token_module.create_auth_token_with_api_key(request, "test-api-key"))
    assert captured[0]["role"] == "public"
    assert captured[0]["channel"] == "test-client"


@pytest.mark.parametrize("language", ["en", "hi", "as", "bn", "gu", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te"])
def test_prompt_template_compiles(language):
    from jinja2 import Environment
    text = (ROOT / "assets/prompts" / f"agrinet_{language}.md").read_text()
    Environment().parse(text)
