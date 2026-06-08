import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic_ai import FinalResultEvent, PartDeltaEvent, PartStartEvent, TextPartDelta
from pydantic_ai.messages import TextPart

from app.services import chat


class ChatSSETests(unittest.IsolatedAsyncioTestCase):
    def test_format_sse_data_preserves_multiline_text(self):
        self.assertEqual(chat._format_sse_data("hello"), "data: hello\n\n")
        self.assertEqual(
            chat._format_sse_data("line 1\nline 2\n"),
            "data: line 1\ndata: line 2\ndata: \n\n",
        )

    def test_parse_keepalive_interval_falls_back_for_bad_values(self):
        with patch.object(chat.logger, "warning") as warning:
            self.assertEqual(chat._parse_sse_keepalive_interval(None), 3.0)
            self.assertEqual(chat._parse_sse_keepalive_interval(""), 3.0)
            self.assertEqual(chat._parse_sse_keepalive_interval("bad"), 3.0)
            self.assertEqual(chat._parse_sse_keepalive_interval("0"), 3.0)
            self.assertEqual(chat._parse_sse_keepalive_interval("nan"), 3.0)
            self.assertEqual(chat._parse_sse_keepalive_interval("1.5"), 1.5)

        self.assertEqual(warning.call_count, 3)

    async def test_keepalive_helper_wraps_string_results(self):
        async def slow_string():
            await asyncio.sleep(0.03)
            return "done"

        items = []
        async for item in chat._await_with_sse_keepalives(
            slow_string(),
            interval_s=0.01,
        ):
            items.append(item)

        self.assertGreaterEqual(items.count(chat.SSE_KEEPALIVE), 1)
        self.assertIsInstance(items[-1], chat._AwaitedResult)
        self.assertEqual(items[-1].value, "done")

    async def test_stream_agrinet_emits_answer_deltas_as_sse_data(self):
        class Result:
            output = "Hello world"

            def usage(self):
                return SimpleNamespace(request_tokens=2, response_tokens=3)

        async def fake_events(**_kwargs):
            yield PartStartEvent(index=0, part=TextPart(content="Hello "))
            yield FinalResultEvent(tool_name=None, tool_call_id=None)
            yield PartDeltaEvent(
                index=0,
                delta=TextPartDelta(content_delta="world"),
            )
            yield SimpleNamespace(event_kind="agent_run_result", result=Result())

        fake_agent = SimpleNamespace(run_stream_events=fake_events)

        with (
            patch.object(chat, "agrinet_agent", fake_agent),
            patch.object(chat, "lf_update_current_observation"),
            patch.object(chat, "lf_set_trace_io"),
        ):
            chunks = []
            async for item in chat._stream_agrinet(
                user_message="message",
                trimmed_history=[],
                deps=SimpleNamespace(),
                session_id="s1",
                user_id="u1",
                query="question",
                moderation_category="valid_agricultural",
            ):
                chunks.append(item)

        self.assertEqual(chunks[0], "data: Hello \n\n")
        self.assertEqual(chunks[1], "data: world\n\n")
        self.assertIsInstance(chunks[2], chat._AwaitedResult)
        self.assertEqual(chunks[2].value.output, "Hello world")

    async def test_stream_chat_messages_emits_streamed_sse_chunks(self):
        class Moderation:
            category = "valid_agricultural"

            def __str__(self):
                return "moderation-ok"

        class Result:
            output = "final-answer"

            def new_messages(self):
                return []

        async def fake_moderation(_user_message, _session_id):
            return Moderation()

        async def fake_stream_agrinet(**_kwargs):
            yield "data: final-\n\n"
            yield "data: answer\n\n"
            yield chat._AwaitedResult(Result())

        async def fake_update_history(_session_id, _messages):
            return None

        fake_client = SimpleNamespace(flush=lambda: None)

        with (
            patch.object(chat, "_run_moderation", fake_moderation),
            patch.object(chat, "_stream_agrinet", fake_stream_agrinet),
            patch.object(chat, "update_message_history", fake_update_history),
            patch.object(chat, "get_client", lambda: fake_client),
        ):
            chunks = []
            async for chunk in chat.stream_chat_messages(
                query="when to sow wheat?",
                session_id="s1",
                source_lang="en",
                target_lang="en",
                user_id="u1",
                history=[],
                background_tasks=SimpleNamespace(),
                channel="BharatVistaar",
            ):
                chunks.append(chunk)

        self.assertEqual(chunks[0], chat.SSE_KEEPALIVE)
        self.assertIn("data: final-\n\n", chunks)
        self.assertIn("data: answer\n\n", chunks)
        self.assertEqual(chunks.count("data: final-answer\n\n"), 0)


if __name__ == "__main__":
    unittest.main()
