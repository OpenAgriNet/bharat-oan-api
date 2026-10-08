"""Slow upstream calls must not freeze the event loop.

Every chat stream on a uvicorn worker shares one event loop. A blocking HTTP
call inside an ``async def`` stalls all of them until the upstream answers.
Each test runs a 50 ms heartbeat next to the call and checks it kept ticking.
"""
import asyncio
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from agents.deps import FarmerContext
from agents.tools import mandi, weather
from app.auth.jwt_auth import get_current_user
from app.routers import transcribe as transcribe_router, tts as tts_router

DELAY = 0.5
MAX_STALL = 0.3


def _slow(result):
    def call(*args, **kwargs):
        time.sleep(DELAY)
        return result
    return call


async def _longest_stall(make_call):
    stop = asyncio.Event()
    gaps = []

    async def heartbeat():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.1)
    started = time.perf_counter()
    await make_call()
    elapsed = time.perf_counter() - started
    stop.set()
    await beat
    assert elapsed >= DELAY, "the slow upstream was never called"
    return max(gaps)


def _ctx():
    return SimpleNamespace(deps=FarmerContext(query="q", session_id="s1"))


def test_weather_forecast_does_not_block_event_loop(monkeypatch):
    class SlowBap(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(DELAY)
            body = json.dumps({"context": {"action": "on_search", "timestamp": "t", "message_id": "m",
                                           "transaction_id": "t", "domain": "d", "version": "1.1.0"},
                               "responses": []}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), SlowBap) as server:
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        monkeypatch.setenv("BAP_ENDPOINT", f"http://127.0.0.1:{server.server_address[1]}")
        try:
            stall = asyncio.run(_longest_stall(lambda: weather.weather_forecast(_ctx(), 25.6, 85.1)))
        finally:
            server.shutdown()
    assert stall < MAX_STALL


def test_mandi_prices_do_not_block_event_loop(monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap.test")
    monkeypatch.setattr(mandi, "_fetch_all_pages", _slow(([], {})))
    stall = asyncio.run(_longest_stall(lambda: mandi.get_mandi_prices(_ctx(), 25.6, 85.1, "Patna", "Onion")))
    assert stall < MAX_STALL


def _client(monkeypatch):
    # ASGITransport runs the app on the test's event loop, next to the heartbeat;
    # TestClient would run it on a separate loop.
    async def no_telemetry(*args, **kwargs):
        return None

    monkeypatch.setattr(tts_router, "text_to_speech_bhashini", _slow(b"audio"))
    monkeypatch.setattr(tts_router, "send_telemetry", no_telemetry)
    monkeypatch.setattr(transcribe_router, "detect_audio_language_bhashini", _slow("hi"))
    monkeypatch.setattr(transcribe_router, "transcribe_bhashini", _slow("namaste"))
    monkeypatch.setattr(transcribe_router, "send_telemetry", no_telemetry)
    app = FastAPI()
    app.include_router(tts_router.router, prefix="/api")
    app.include_router(transcribe_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"mobile": "test"}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_tts_does_not_block_event_loop(monkeypatch):
    async def call():
        async with _client(monkeypatch) as client:
            response = await client.post("/api/tts/", json={"text": "namaste", "target_lang": "hi"})
            assert response.status_code == 200

    assert asyncio.run(_longest_stall(call)) < MAX_STALL


def test_transcribe_does_not_block_event_loop(monkeypatch):
    async def call():
        async with _client(monkeypatch) as client:
            response = await client.post(
                "/api/transcribe/",
                json={"audio_content": base64.b64encode(b"x").decode(), "service_type": "bhashini"},
            )
            assert response.status_code == 200

    assert asyncio.run(_longest_stall(call)) < MAX_STALL
