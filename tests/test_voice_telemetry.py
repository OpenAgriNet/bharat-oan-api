"""Voice telemetry stays separate from chat telemetry and keeps voice-oan-api's payloads.

The dashboard processor routes on these exact shapes (OE_VOICE_RESPONSE, TeleFeedback,
and no ttsResponseDetails/asrResponseDetails keys on voice events), so they are pinned.
"""
import ast
import asyncio
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from app.voice import telemetry as voice_telemetry
from helpers.telemetry.base import TelemetryRequest
from helpers.telemetry.voice import create_feedback_event, create_voice_response_event

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = "Bharat Vistaar Voice AI"
VOICE_TARGET = {"id": "default", "ver": "v0.1", "parent": {"id": "p1", "type": "default"}}


def envelope(event):
    return TelemetryRequest(events=[event], ets=1).model_dump(mode="json")


def test_voice_turn_event_matches_voice_service_payload():
    event = create_voice_response_event(
        uid="u1", session_id="s1", channel=CHANNEL, question_text="q?", qid="Q",
        source_lang="hi", target_lang="hi", response_text="ans", timestamp=1,
    )
    sent = envelope(event)["events"][0]
    assert sent["eid"] == "OE_VOICE_RESPONSE"
    assert sent["channel"] == CHANNEL and sent["pdata"] == {"id": "BharatVistaar-voice", "ver": "v0.1", "pid": None}
    assert sent["did"] == "default-email" and sent["sid"] == "s1" and sent["uid"] == "u1"
    assert sent["edata"]["eks"] == {"qid": "Q", "target": {
        **VOICE_TARGET, "type": "VoiceQuestion", "telefeedbackDetails": None,
        "questionsDetails": {"questionText": "q?", "responseText": "ans", "sourceLang": "hi", "targetLang": "hi"},
    }}


def test_welcome_turn_omits_question_text():
    event = create_voice_response_event(
        uid="u1", session_id="s1", channel=CHANNEL, qid="W", source_lang="hi",
        target_lang="hi", response_text="welcome", timestamp=1,
    )
    details = envelope(event)["events"][0]["edata"]["eks"]["target"]["questionsDetails"]
    assert details == {"responseText": "welcome", "sourceLang": "hi", "targetLang": "hi"}


def test_feedback_event_matches_voice_service_payload():
    event = create_feedback_event(
        session_id="s1", feedback_type="like", feedback_text="good", uid="u1", channel=CHANNEL, timestamp=1,
    )
    sent = envelope(event)["events"][0]
    assert sent["eid"] == "OE_ITEM_RESPONSE" and sent["did"] == "DEFAULT-USER"
    assert sent["pdata"]["id"] == "BharatVistaar"
    assert sent["edata"]["eks"] == {"target": {
        **VOICE_TARGET, "type": "TeleFeedback", "questionsDetails": None,
        "telefeedbackDetails": {"feedbackText": "good", "sessionId": "s1", "feedbackType": "like"},
    }}


@pytest.mark.parametrize("event", [
    create_voice_response_event(uid="u", session_id="s", channel=CHANNEL, timestamp=1),
    create_feedback_event(session_id="s", feedback_type="dislike", feedback_text="", uid="u", channel=CHANNEL),
])
def test_voice_events_never_look_like_tts_or_asr_events(event):
    target = envelope(event)["events"][0]["edata"]["eks"]["target"]
    assert not {"ttsResponseDetails", "asrResponseDetails", "feedbackDetails", "errorDetails"} & set(target)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


VOICE_FILES = [*(ROOT / "app/voice").glob("*.py"), ROOT / "agents/voice.py", ROOT / "agents/tools/feedback.py"]
CHAT_FILES = [ROOT / "app/services/chat.py", ROOT / "app/tasks/telemetry.py",
              *(ROOT / "app/routers").glob("*.py"), ROOT / "helpers/telemetry/chat.py"]


@pytest.mark.parametrize("path", VOICE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_voice_code_uses_only_voice_telemetry(path):
    imports = _imports(path)
    assert not any(name.startswith(("helpers.telemetry.chat", "app.tasks.telemetry")) for name in imports)
    source = path.read_text()
    assert "settings.telemetry_" not in source and 'getenv("TELEMETRY_' not in source


@pytest.mark.parametrize("path", CHAT_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_chat_code_uses_only_chat_telemetry(path):
    imports = _imports(path)
    assert not any(name.startswith(("helpers.telemetry.voice", "app.voice")) for name in imports)


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx.Response(self.statuses.pop(0), request=request)


@pytest.fixture
def transport(monkeypatch):
    holder = {}
    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=holder["transport"], **kwargs)

    monkeypatch.setattr(voice_telemetry.httpx, "AsyncClient", client)
    monkeypatch.setattr(voice_telemetry.settings, "voice_telemetry_api_url", "https://voice-telemetry.test/v3/telemetry")
    monkeypatch.setattr(voice_telemetry.settings, "telemetry_api_url", "https://chat-telemetry.test/never-used")
    for name in ("token", "key", "secret"):
        monkeypatch.setattr(voice_telemetry.settings, f"voice_telemetry_auth_{name}", None)
        monkeypatch.setattr(voice_telemetry.settings, f"telemetry_auth_{name}", "chat-secret")
    monkeypatch.setattr(voice_telemetry, "_RETRY_WAIT", wait_none())

    def install(*statuses):
        holder["transport"] = _Transport(statuses)
        return holder["transport"]
    return install


def test_sender_posts_to_voice_url_without_chat_credentials(transport):
    sent = transport(200)
    response = asyncio.run(voice_telemetry.send_voice_telemetry({"events": []}))
    assert response.status_code == 200
    (request,) = sent.requests
    assert str(request.url) == "https://voice-telemetry.test/v3/telemetry"
    assert "authorization" not in request.headers


def test_sender_retries_5xx_and_429_then_gives_up(transport):
    sent = transport(503, 429, 200)
    assert asyncio.run(voice_telemetry.send_voice_telemetry({})).status_code == 200
    assert len(sent.requests) == 3
    sent = transport(500, 500, 500)
    assert asyncio.run(voice_telemetry.send_voice_telemetry({})) is None
    assert len(sent.requests) == 3


def test_sender_signs_only_with_voice_credentials(transport, monkeypatch):
    monkeypatch.setattr(voice_telemetry.settings, "voice_telemetry_auth_token", "voice-token")
    sent = transport(200)
    asyncio.run(voice_telemetry.send_voice_telemetry({}))
    assert sent.requests[0].headers["authorization"] == "Bearer voice-token"


def test_sender_skips_when_voice_url_unset(transport, monkeypatch):
    monkeypatch.setattr(voice_telemetry.settings, "voice_telemetry_api_url", "")
    sent = transport()
    assert asyncio.run(voice_telemetry.send_voice_telemetry({})) is None
    assert not sent.requests
