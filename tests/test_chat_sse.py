import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    async def test_stream_chat_messages_emits_final_answer_as_sse_data(self):
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

        async def fake_agrinet(**_kwargs):
            return Result()

        async def fake_update_history(_session_id, _messages):
            return None

        fake_client = SimpleNamespace(flush=lambda: None)

        with (
            patch.object(chat, "_run_moderation", fake_moderation),
            patch.object(chat, "_run_agrinet", fake_agrinet),
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
        self.assertEqual(chunks[-1], "data: final-answer\n\n")


if __name__ == "__main__":
    unittest.main()
