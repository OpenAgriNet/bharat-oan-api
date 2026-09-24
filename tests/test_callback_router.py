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

    async def fake_persist(_event):
        return None

    monkeypatch.setattr(callback_router.cache, "set", fake_cache_set)
    monkeypatch.setattr(callback_router.cache, "get", fake_cache_get)
    monkeypatch.setattr(callback_router, "_persist_callback_audit", fake_persist)

    return TestClient(app)


def test_callback_post_stores_status_and_supports_nested_session_id(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.post(
        "/api/callback?from=agristack",
        json={
            "data": {
                "sessionId": "ABC123",
                "farmerId": "FARMER-9",
                "consentName": "land_records",
                "consentMessage": "farmer granted consent",
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "received"
    assert body["callbackSessionId"] == "ABC123"
    assert body["farmerId"] == "FARMER-9"
    assert body["consentName"] == "land_records"
    assert body["consentMessage"] == "farmer granted consent"
    assert body["cache_status"] == "stored"

    status_response = client.get("/api/callback/status?from=agristack&callbackSessionId=ABC123")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "received"
    assert status_body["callbackSessionId"] == "ABC123"
    assert status_body["farmerId"] == "FARMER-9"


def test_callback_status_not_found(monkeypatch):
    client = _build_test_client(monkeypatch)

    response = client.get("/api/callback/status?from=agristack&callbackSessionId=UNKNOWN")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["callbackSessionId"] == "UNKNOWN"
