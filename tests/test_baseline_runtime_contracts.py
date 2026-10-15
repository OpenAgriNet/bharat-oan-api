"""Offline checks for the production helpers restored during branch reconciliation."""
import asyncio
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from pydantic_ai import AgentRunResultEvent, FinalResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import ModelResponse, TextPart, TextPartDelta

from tests.test_production_reconciliation import load_module, token_module


def stub(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.mark.parametrize("metadata, expected", [
    (None, {}), ({"surface": "mobile"}, {"surface": "mobile"}),
    ('{"surface":"mobile"}', {"surface": "mobile"}),
    ('[1,2]', {"raw": '[1,2]'}), ('not-json', {"raw": 'not-json'}),
    ([1, 2], {"raw": '[1, 2]'}),
])
def test_guest_metadata_and_expiry(token_module, monkeypatch, metadata, expected):
    captured = []
    monkeypatch.setattr(token_module, "private_key", object())
    monkeypatch.setattr(token_module.jwt, "encode", lambda payload, *a, **kw: captured.append(payload) or "token")
    request = token_module.AuthRequest(metadata=metadata, fingerprint_id="device", mobile="9876543210")
    result = asyncio.run(token_module.create_auth_token(request))
    payload = captured[0]
    assert payload["metadata"] == {"surface": "public_chat", **expected, "fingerprint_id": "device"}
    assert payload["role"] == "public" and payload["sub"] == "guest:device"
    assert payload["mobile"] == "9876543210"
    assert payload["exp"] - payload["iat"] == result.expires_in == 900
    if isinstance(metadata, dict):
        assert "fingerprint_id" not in metadata


@pytest.fixture
def npss(load_module, monkeypatch):
    stub(monkeypatch, "app.core.cache", cache=SimpleNamespace())
    stub(monkeypatch, "app.core.npss_geocodes", resolve_npss_location_ids=AsyncMock(return_value={}))
    stub(monkeypatch, "app.core.image_storage", mark_processed=lambda *a: None, cleanup_image=lambda *a: None)
    module = load_module("agents/tools/npss.py")
    module._get_cached_npss_token = AsyncMock(return_value="token")
    module._download_image = AsyncMock(return_value=(b"\xff\xd8\xffimage", "application/octet-stream"))
    module._call_npss_analyze = AsyncMock(return_value={"pest": "Rust", "crop": "Wheat", "details": {"risk": ["high"]}})
    return module


@pytest.mark.parametrize("geo, expected", [
    ({}, None),
    ({"state_id": "1", "district_id": "2"}, None),
    ({"state_id": "1", "district_id": "2", "sub_district_id": "3"}, "0"),
    ({"state_id": "1", "district_id": "2", "sub_district_id": "3", "village_id": "4"}, "4"),
])
def test_npss_location_fallback_and_complete_result(npss, geo, expected):
    npss.resolve_npss_location_ids.return_value = geo
    recorded = []
    deps = SimpleNamespace(latitude=28.6, longitude=77.2, mark_npss_result=lambda **kw: recorded.append(kw))
    output = asyncio.run(npss.analyze_crop_image(SimpleNamespace(deps=deps), "https://image.test/photo.jpg", location="ignored"))
    sent = npss._call_npss_analyze.call_args.kwargs
    assert sent["village_id"] == (expected or npss.NPSS_FALLBACK_LOCATION["village_id"])
    assert sent["latitude"] == (28.6 if expected else npss.NPSS_FALLBACK_LOCATION["latitude"])
    assert sent["mimetype"] == "image/jpeg"
    assert "**pest:** Rust" in output and "high" in output and "**Source:** NPSS" in output
    assert recorded[0]["raw_result"]["details"] == {"risk": ["high"]}


@pytest.mark.parametrize("data, mime, message", [
    (b"RIFF1234WEBPimage", "image/webp", "WebP images are not supported"),
    (b"unknown", "application/octet-stream", "could not be recognized"),
    (b"<svg/>", "image/svg+xml", "Unsupported image format"),
])
def test_npss_rejects_unsupported_images_without_analysis(npss, data, mime, message):
    npss._download_image.return_value = (data, mime)
    ctx = SimpleNamespace(deps=SimpleNamespace(latitude=None, longitude=None))
    assert message in asyncio.run(npss.analyze_crop_image(ctx, "https://image.test/photo"))
    npss._call_npss_analyze.assert_not_called()


def test_pending_image_is_cleared_by_latest_analysis(load_module):
    module = load_module("app/core/npss_followup.py")
    pending = SimpleNamespace(parts=[SimpleNamespace(part_kind="tool-return", content="[NPSS_LOCATION_REQUIRED] [IMAGE_URL: https://image.test/1]")])
    finished = SimpleNamespace(parts=[SimpleNamespace(part_kind="tool-return", tool_name="analyze_crop_image", content="Service unavailable")])
    assert module.find_pending_npss_image_url([pending]) == "https://image.test/1"
    assert module.find_pending_npss_image_url([pending, finished]) is None


@pytest.fixture
def chat(load_module, monkeypatch):
    deps = load_module("agents/deps.py").FarmerContext
    stub(monkeypatch, "agents.deps", FarmerContext=deps)
    stub(monkeypatch, "agents.agrinet", agrinet_agent=SimpleNamespace())
    stub(monkeypatch, "agents.tools.agristack", get_agristack_link=AsyncMock(return_value=None))
    stub(monkeypatch, "agents.moderation", moderation_agent=SimpleNamespace())
    registry = SimpleNamespace(get_model_name=lambda a: a, get_default_alias=lambda u: "mini", get_model=lambda a: a)
    stub(monkeypatch, "agents.model_registry", get_registry=lambda: registry)
    decision = SimpleNamespace(route="mini", model_name="mini", source="redis")
    router = SimpleNamespace(resolve=AsyncMock(return_value=decision))
    stub(monkeypatch, "app.services.model_routing", ModelRouteDecision=object, get_model_router=lambda: router)
    stub(monkeypatch, "app.config", settings=SimpleNamespace(langfuse_tracing_environment="test"))
    stub(monkeypatch, "app.services.chat_turn_map", write_chat_turn_map=AsyncMock())
    stub(monkeypatch, "helpers.translation", BhashiniTranslator=object, BaseTranslator=object)
    monkeypatch.setattr(sys.modules["helpers.utils"], "post_process_translation", lambda x: x, raising=False)
    response = load_module("app/services/npss_response.py")
    stub(monkeypatch, "app.services.npss_response", post_process_npss_response=response.post_process_npss_response)
    stub(monkeypatch, "app.tasks.telemetry", send_telemetry=AsyncMock())
    stub(monkeypatch, "app.utils", filter_thinking_from_history=lambda m: m,
         format_message_pairs=lambda *a: [], trim_history=lambda h, **kw: h, update_message_history=AsyncMock())
    stub(monkeypatch, "helpers.langfuse_trace_schema", AGENT_MODERATION="moderation", AGENT_VISTAAR="vistaar", chat_trace_metadata_strings=lambda **kw: {})
    stub(monkeypatch, "helpers.langfuse_tracing", lf_update_current_observation=lambda **kw: None, lf_update_current_span=lambda **kw: None)
    stub(monkeypatch, "helpers.telemetry", TelemetryRequest=object, create_frontend_compatible_item_batch=lambda e: [e],
         create_chat_answer_event=lambda **kw: {"type": "answer", **kw},
         create_chat_question_event=lambda **kw: {"type": "question", **kw},
         create_chat_error_event=lambda **kw: {"type": "error", **kw})
    stub(monkeypatch, "langfuse", observe=lambda **kw: lambda f: f, propagate_attributes=lambda **kw: nullcontext(),
         get_client=lambda: SimpleNamespace(flush=lambda: None, get_current_trace_id=lambda: "trace"))
    module = load_module("app/services/chat.py")
    module._run_moderation = AsyncMock(return_value=SimpleNamespace(category="agriculture"))
    module.events = []
    module._queue_telemetry_event = lambda bg, event: module.events.append(event)
    module._send_telemetry_event = AsyncMock(side_effect=lambda event: module.events.append(event))
    module.decision = decision
    return module


@pytest.mark.parametrize("disconnect", ["none", "mid_stream", "npss_output"])
def test_stream_and_disconnect_save_one_complete_turn(chat, disconnect):
    async def check():
        finish = asyncio.Event()
        image = disconnect == "npss_output"
        output = "**Pest:** Rust\n\nSymptoms." if image else "Hello farmer"
        result = SimpleNamespace(new_messages=lambda: [ModelResponse(parts=[TextPart(output)])])

        async def run(**kw):
            kw["deps"].npss_used = image
            await kw["chunk_queue"].put(output)
            if disconnect == "mid_stream":
                await finish.wait()
            await kw["chunk_queue"].put(None)
            return chat._AgrinetCompletedRun(result, output), chat.decision, False

        chat._run_agrinet_with_failover_streaming = run
        history = [ModelResponse(parts=[TextPart("earlier turn")])]
        stream = chat.stream_chat_messages(query="help", session_id="session", source_lang="en", target_lang="en",
            user_id="farmer", history=history, background_tasks=BackgroundTasks(), qid="question", is_image_analysis=image)
        first = await anext(stream)
        expected = output + "\n\n**Source: NPSS**" if image else output
        assert first == expected
        if disconnect == "none":
            assert [chunk async for chunk in stream] == []
        else:
            await stream.aclose()
            finish.set()
            await asyncio.wait_for(asyncio.gather(*list(chat._detached_finalizers)), 1)
        chat.update_message_history.assert_awaited_once()
        session, messages = chat.update_message_history.call_args.args
        assert session == "session" and len(messages) == 2
        assert messages[-1].parts[0].content == expected
        assert history[0].parts[0].content == "earlier turn"
        answers = [event for event in chat.events if event["type"] == "answer"]
        assert len(answers) == 1 and answers[0]["answer_text"] == expected
        assert answers[0]["qid"] == "question"
        assert chat._send_telemetry_event.await_count == (disconnect != "none")
    asyncio.run(check())


def test_first_stream_tokens_are_not_lost(chat):
    result = SimpleNamespace(output="Hello farmer", usage=lambda: SimpleNamespace(input_tokens=1, output_tokens=2))
    async def events(**kw):
        yield PartStartEvent(index=0, part=TextPart("Hello"))
        yield FinalResultEvent(tool_name=None, tool_call_id=None)
        yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" farmer"))
        yield AgentRunResultEvent(result=result)
    chat.agrinet_agent.run_stream_events = events
    async def check():
        queue = asyncio.Queue()
        completed = await chat._run_agrinet_once_streaming("query", [], None, "s", "u", "agriculture", chat.decision, False, chat._StreamChunkSink(queue))
        chunks = [queue.get_nowait() for _ in range(queue.qsize())]
        assert "".join(chunks) == completed.output_text == "Hello farmer"
    asyncio.run(check())


def test_disconnect_timeout_cancels_pending_run_without_saving_partial_answer(chat):
    async def check():
        chat._DISCONNECT_FINALIZE_TIMEOUT_SECONDS = 0.01
        pending = asyncio.create_task(asyncio.Event().wait())
        ctx = SimpleNamespace(session_id="session")
        await chat._finalize_after_client_disconnect(ctx, pending)
        assert pending.cancelled()
        chat.update_message_history.assert_not_awaited()
        assert chat.events == []
    asyncio.run(check())


def test_failed_stream_records_error_without_completed_answer(chat):
    async def run(**kw):
        await kw["chunk_queue"].put(None)
        raise RuntimeError("model failed")
    chat._run_agrinet_with_failover_streaming = run
    async def check():
        stream = chat.stream_chat_messages(query="help", session_id="s", source_lang="en", target_lang="en",
            user_id="u", history=[], background_tasks=BackgroundTasks(), qid="q")
        with pytest.raises(RuntimeError, match="model failed"):
            await anext(stream)
        assert [e["type"] for e in chat.events] == ["question", "error"]
        chat.update_message_history.assert_not_awaited()
    asyncio.run(check())


def test_npss_translation_preserves_labels_and_existing_hindi(chat):
    calls = []
    def translator(source, target):
        assert (source, target) == ("en", "hi")
        def translate(texts):
            calls.append(texts)
            return ["रोग", "पत्ते पीले हैं।"]
        return SimpleNamespace(translate_texts=translate)
    output = chat.post_process_npss_response(text="**Pest:** Rust\n**Crop:** गेहूँ\n\nLeaves are yellow.",
        target_lang="hi", npss_used=True, translator_factory=translator)
    assert calls == [["Rust", "Leaves are yellow."]]
    assert "**कीट:** रोग" in output and "**फसल:** गेहूँ" in output
    assert "पत्ते पीले हैं।" in output and "**स्रोत: NPSS**" in output
