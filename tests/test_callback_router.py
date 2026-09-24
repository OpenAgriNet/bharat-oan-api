from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import callback as callback_router


def _build_test_client(monkeypatch):
    app = FastAPI()
    app.include_router(callback_router.router, prefix="/api")

    store = {}

    async def fake_cache_set(key, value, ttl=None, namespace=None):
        store[(namespace, key)] = value
        return True

    async def fake_cache_get(key, namespace=None):
        return store.get((namespace, key))

    monkeypatch.setattr(callback_router.cache, "set", fake_cache_set)
    monkeypatch.setattr(callback_router.cache, "get", fake_cache_get)

    return TestClient(app)


def test_callback_post_stores_status_and_supports_nested_session_id(monkeypatch):
    client = _build_test_client(monkeypatch)

    payload = {
        "data": {
            "sessionId": "ABC123",
            "farmerId": "FARMER-9",
            "consentName": "land_records",
            "consentMessage": "farmer granted consent",
        }
    }
    response = client.post("/api/callback?from=agristack", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "received"
    assert body["callbackSessionId"] == "ABC123"
    assert body["from"] == "agristack"
    assert body["body_type"] == "json"
    assert body["body"] == payload

    status_response = client.get("/api/callback/status?from=agristack&callbackSessionId=ABC123")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "received"
    assert status_body["callbackSessionId"] == "ABC123"
    assert status_body["source"] == "agristack"
    assert status_body["body"]["data"]["farmerId"] == "FARMER-9"


def test_callback_status_not_found(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.get("/api/callback/status?from=agristack&callbackSessionId=UNKNOWN")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["callbackSessionId"] == "UNKNOWN"


def test_callback_without_session_id_is_accepted(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.post("/api/callback?from=agristack", json={"farmerId": "FARMER-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "received"
    assert body["callbackSessionId"] is None


def test_callback_malformed_json_does_not_fail(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.post(
        "/api/callback?from=agristack&callbackSessionId=BAD1",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["body_type"] == "raw"
    assert body["callbackSessionId"] == "BAD1"
    assert client.get("/api/callback/status?callbackSessionId=BAD1").json()["status"] == "received"


def test_callback_form_body_session_id(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.post("/api/callback", data={"sessionId": "FORM1"})

    assert response.status_code == 200
    body = response.json()
    assert body["body_type"] == "form"
    assert body["callbackSessionId"] == "FORM1"


def test_callback_status_after_expiry_returns_not_found(monkeypatch):
    client = _build_test_client(monkeypatch)
    client.post("/api/callback?callbackSessionId=EXP1", json={})

    # Simulate Redis TTL expiry: key no longer present.
    async def expired_get(key, namespace=None):
        return None

    monkeypatch.setattr(callback_router.cache, "get", expired_get)

    body = client.get("/api/callback/status?callbackSessionId=EXP1").json()
    assert body["status"] == "not_found"


def test_callback_cache_failure_still_returns_200(monkeypatch):
    client = _build_test_client(monkeypatch)

    async def failing_set(*_args, **_kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(callback_router.cache, "set", failing_set)

    response = client.post("/api/callback?callbackSessionId=R1", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "received"
