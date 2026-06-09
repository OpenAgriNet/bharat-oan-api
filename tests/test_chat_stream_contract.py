import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.jwt_auth import get_current_user
from app.routers import chat as chat_router
from app.services import chat as chat_service


async def _collect_async_generator(generator):
    items = []
    async for item in generator:
        items.append(item)
    return items


def test_keepalive_waiter_emits_marker_before_string_result(monkeypatch):
    async def delayed_string():
        await asyncio.sleep(0.01)
        return "final-result"

    monkeypatch.setattr(chat_service, "CHAT_SSE_KEEPALIVE_INTERVAL_S", 0.001)

    items = asyncio.run(_collect_async_generator(chat_service._wait_with_keepalive(delayed_string())))

    assert chat_service.SSE_KEEPALIVE in items
    assert items[-1] == chat_service._AwaitedResult("final-result")


def test_chat_endpoint_returns_backend_owned_qid_header(monkeypatch):
    app = FastAPI()

    def fake_current_user():
        return {"mobile": "9999999999", "channel": "BharatVistaar"}

    async def fake_history(_session_id):
        await asyncio.sleep(0)
        return []

    async def fake_stream_chat_messages(**_kwargs):
        yield "assistant output"

    monkeypatch.setattr(chat_router, "_get_message_history", fake_history)
    monkeypatch.setattr(chat_router, "stream_chat_messages", fake_stream_chat_messages)
    monkeypatch.setattr(
        chat_router.uuid,
        "uuid4",
        lambda: uuid.UUID("00000000-0000-0000-0000-000000000123"),
    )

    app.dependency_overrides[get_current_user] = fake_current_user
    app.include_router(chat_router.router, prefix="/api")

    client = TestClient(app)
    response = client.get(
        "/api/chat/",
        params={
            "query": "hello",
            "session_id": "session-1",
            "qid": "client-supplied-qid",
            "source_lang": "hi",
            "target_lang": "hi",
            "user_id": "anonymous",
        },
    )

    assert response.status_code == 200
    assert response.text == "assistant output"
    assert response.headers["x-qid"] == "00000000-0000-0000-0000-000000000123"
    assert response.headers["access-control-expose-headers"] == "X-QID"
