"""Exercise the real chat runner and client request, with offline model transport."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from agents.model_registry import ModelRegistry
from tests.test_guided_decoding import MARKDOWN, WORDS


@pytest.mark.parametrize("kind,language,gated", [
    ("vllm", "kn", True), ("vllm", "gu", True), ("vllm", "hi", False),
    ("openai", "kn", False), ("azure-openai", "kn", False),
])
@pytest.mark.parametrize("stream_to_ui", [True, False])
def test_chat_request_tool_roundtrip_and_visible_markdown(monkeypatch, kind, language, gated, stream_to_ui):
    # Imports must not require real credentials or initialize reachable provider clients.
    monkeypatch.setenv("AZURE_GPT54_MINI_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("AZURE_GPT54_MINI_API_KEY", "test")
    monkeypatch.setenv("AGRINET_GEMMA_BASE_URL", "http://unused.invalid/v1")
    monkeypatch.setenv("AGRINET_GEMMA_MODEL_NAME", "test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    from app.services import chat
    from app.services.model_routing import ModelRouteDecision

    text = MARKDOWN.format(word=WORDS.get(language, "नमस्ते"), lang=language).strip()
    requests = []
    tool_calls = []

    async def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        assert ("structured_outputs" in body) is gated
        assert body["temperature"] == 0.7
        assert body["top_p"] == 0.95
        assert body["max_completion_tokens"] == 4096
        assert body["tools"][0]["function"]["name"] == "lookup_crop"
        if len(requests) == 1:
            deltas = [{"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "lookup_crop", "arguments": '{"crop_id":"wheat"}'}}]}]
            finish = "tool_calls"
        else:
            assert any(m["role"] == "tool" and "2750" in m["content"] for m in body["messages"])
            # Exercise both initial text and later deltas across Markdown blocks.
            split = text.index("\n\n") + 2
            deltas = [{"role": "assistant", "content": text[:split]}, {"content": text[split:]}]
            finish = "stop"
        chunks = [json.dumps({"id": "chatcmpl-test", "created": 1, "model": "test", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) for delta in deltas]
        chunks.append(json.dumps({"id": "chatcmpl-test", "created": 1, "model": "test", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(f"data: {chunk}\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = AsyncOpenAI(api_key="test", base_url="http://unused.invalid/v1", http_client=http, max_retries=0)
            model = OpenAIChatModel("test", provider=OpenAIProvider(openai_client=client))
            agent = Agent(model, model_settings={"temperature": 0.7, "top_p": 0.95, "max_tokens": 4096, "parallel_tool_calls": True})

            @agent.tool_plain
            async def lookup_crop(crop_id: str) -> dict:
                tool_calls.append(crop_id)
                return {"price": 2750}

            registry = ModelRegistry()
            registry._models_cfg["selected"] = {"kind": kind, "model_name": "test"}
            registry._model_cache["selected"] = model
            monkeypatch.setattr(chat, "agrinet_agent", agent)
            monkeypatch.setattr(chat, "get_registry", lambda: registry)
            monkeypatch.setattr(chat, "lf_update_current_observation", lambda **kwargs: None)
            queue = asyncio.Queue()
            result = await chat._run_agrinet_once_streaming(
                "price", [], SimpleNamespace(lang_code=language), "test", "test", "valid",
                ModelRouteDecision("selected", "test", "test"), False,
                chat._StreamChunkSink(queue) if stream_to_ui else None,
            )
            assert result.output_text == text
            if stream_to_ui:
                assert "".join(queue.get_nowait() for _ in range(queue.qsize())) == text
            assert tool_calls == ["wheat"]
            assert len(requests) == 2

    async def bounded():
        return await asyncio.wait_for(run(), timeout=10)

    asyncio.run(bounded())


@pytest.mark.parametrize("chunks", [
    ["₹3,0", "0", "0"],
    ["*", "*", "bold", "*", "*"],
    ["`", "`", "`", "json\n{}\n", "`", "`", "`"],
    ["#", "#", " Heading\n", "\n", "Text"],
    ["ha", "ha"],
    ["| Price |\n| ", "-", "-", "-", " |\n| ₹3,0", "00", " |"],
    ["A legitimate repeated line.\n", "A legitimate repeated line.\n"],
])
def test_stream_preserves_append_only_deltas(chunks):
    from app.services import chat

    async def run():
        queue = asyncio.Queue()
        state = chat._AgrinetStreamState()
        sink = chat._StreamChunkSink(queue)
        for chunk in chunks:
            await chat._emit_final_text_chunk(chunk, state, sink)
        assert "".join(queue.get_nowait() for _ in range(queue.qsize())) == "".join(chunks)

    asyncio.run(run())
