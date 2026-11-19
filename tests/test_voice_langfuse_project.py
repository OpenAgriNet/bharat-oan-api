"""Chat and voice trace to different Langfuse projects inside one process."""
import asyncio
import os

import pytest
from langfuse._client.get_client import _current_public_key

from helpers import langfuse_helper

VOICE_PATH = "/api/v1/chat/completions"


@pytest.fixture
def two_projects(monkeypatch):
    monkeypatch.setattr(langfuse_helper, "CHAT_PUBLIC_KEY", "pk-chat")
    monkeypatch.setattr(langfuse_helper, "VOICE_PUBLIC_KEY", "pk-voice")


def seen_key(path, root_path=""):
    seen = {}

    async def app(scope, receive, send):
        seen["key"] = _current_public_key.get(None)

    middleware = langfuse_helper.LangfuseProjectMiddleware(app, voice_path=VOICE_PATH)
    asyncio.run(middleware({"type": "http", "path": path, "root_path": root_path}, None, None))
    return seen["key"]


def test_voice_endpoint_runs_under_the_voice_project(two_projects):
    assert seen_key(VOICE_PATH) == "pk-voice"
    assert seen_key(VOICE_PATH + "/") == "pk-voice"
    assert seen_key("/v1/chat/completions", root_path="/api") == "pk-voice"


@pytest.mark.parametrize("path", ["/api/chat/", "/api/transcribe/", "/api/health", "/api/telemetry"])
def test_every_other_route_runs_under_the_chat_project(two_projects, path):
    assert seen_key(path) == "pk-chat"


def test_project_context_ends_with_the_request(two_projects):
    seen_key(VOICE_PATH)
    assert _current_public_key.get(None) is None


def test_without_voice_keys_there_is_one_project_and_no_context(monkeypatch):
    monkeypatch.setattr(langfuse_helper, "CHAT_PUBLIC_KEY", "pk-chat")
    monkeypatch.setattr(langfuse_helper, "VOICE_PUBLIC_KEY", None)
    assert not langfuse_helper.separate_voice_project()
    assert langfuse_helper.project_public_key("voice") == "pk-chat"
    assert seen_key(VOICE_PATH) is None  # Single project: Langfuse resolves its only client.


def test_voice_tracing_never_shares_the_chat_tracer_provider():
    if not langfuse_helper.separate_voice_project():
        pytest.skip("VOICE_LANGFUSE_* keys not configured in this environment")
    from opentelemetry import trace
    from agents.voice import voice_agent

    voice_provider = langfuse_helper.voice_tracer_provider
    assert voice_provider is not trace.get_tracer_provider()
    # PydanticAI's tracer for the voice agent exports through the voice provider only.
    assert voice_agent.instrument.tracer.span_processor is voice_provider._active_span_processor


def test_voice_telemetry_shares_the_chat_url_unless_overridden():
    s = langfuse_helper.settings
    expected = os.getenv("VOICE_TELEMETRY_API_URL") or s.telemetry_api_url
    assert s.voice_telemetry_api_url == expected
