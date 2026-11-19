"""Voice request-count middleware (dashboard Service Check > Voice); ported from voice-oan-api."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.voice import completion_telemetry as telemetry
from helpers.telemetry import voice as voice_events


class CompletionTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, app, *, path="/api/v1/chat/completions", method="POST",
                          scope_type="http", root_path="", receive=None, send=None):
        messages = []

        async def collect(message):
            messages.append(message)

        scope = {
            "type": scope_type,
            "path": path,
            "method": method,
            "root_path": root_path,
        }
        middleware = telemetry.CompletionTelemetryMiddleware(
            app, path="/api/v1/chat/completions"
        )
        await middleware(scope, receive or AsyncMock(), send or collect)
        return messages

    def setUp(self):
        self.post = AsyncMock(return_value=SimpleNamespace(status_code=200))
        patches = [
            patch.object(telemetry.voice_telemetry, "send_voice_telemetry", self.post),
            patch.object(telemetry.time, "perf_counter", side_effect=[10.0, 10.125]),
            patch.object(telemetry.time, "time", return_value=1793577600),
            patch.object(voice_events.uuid, "uuid4", return_value="request-id"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def event(self):
        self.post.assert_awaited_once()
        payload = self.post.call_args.args[0]
        self.assertEqual(payload["id"], "ekstep.telemetry")
        self.assertEqual(len(payload["events"]), 1)
        event = payload["events"][0]
        self.assertEqual(event["channel"], "BharatVistaar-voice")
        self.assertEqual(event["mid"], "OE_request-id")
        self.assertEqual(event["ets"], 1793577600000)
        details = event["edata"]["eks"]["target"]["networkApiDetails"]
        self.assertEqual(details["apiService"], "voice_chat_completions")
        self.assertEqual(details["apiType"], "VOICE_API")
        self.assertEqual(details["latencyMs"], 125)
        self.assertNotIn("input", details)
        return details

    async def test_stream_counts_once_after_final_body_without_changing_chunks(self):
        chunks = [
            {"type": "http.response.start", "status": 200, "headers": []},
            {"type": "http.response.body", "body": b"private text", "more_body": True},
            {"type": "http.response.body", "body": b"data: [DONE]\n\n"},
        ]

        async def app(scope, receive, send):
            for message in chunks:
                await send(message)
                self.post.assert_not_awaited()

        self.assertEqual(await self.run_request(app), chunks)
        details = self.event()
        self.assertTrue(details["success"])
        self.assertEqual(details["output"]["outcome"], "success")
        self.assertIsNone(details["output"]["errorType"])
        self.assertNotIn("private text", str(self.post.call_args))

    async def test_non_streaming_status_outcomes(self):
        for status, outcome in [(200, "success"), (400, "rejected"), (401, "rejected"),
                                (403, "rejected"), (422, "rejected"), (429, "rejected"),
                                (500, "error"), (503, "error")]:
            with self.subTest(status=status):
                self.post.reset_mock()
                with patch.object(telemetry.time, "perf_counter", side_effect=[10, 10.125]):
                    async def app(scope, receive, send):
                        await send({"type": "http.response.start", "status": status})
                        await send({"type": "http.response.body", "body": b"{}"})

                    await self.run_request(app)
                details = self.event()
                self.assertEqual(details["statusCode"], status)
                self.assertEqual(details["output"]["outcome"], outcome)
                self.assertEqual(details["success"], outcome == "success")

    async def test_exception_after_stream_headers_is_not_success(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            raise RuntimeError("private exception detail")

        with self.assertRaisesRegex(RuntimeError, "private exception detail"):
            await self.run_request(app)
        details = self.event()
        self.assertFalse(details["success"])
        self.assertEqual(details["output"], {"outcome": "error", "errorType": "RuntimeError"})
        self.assertNotIn("private exception detail", str(self.post.call_args))

    async def test_exception_before_headers(self):
        async def app(scope, receive, send):
            raise TimeoutError()

        with self.assertRaises(TimeoutError):
            await self.run_request(app)
        self.assertEqual(self.event()["statusCode"], 500)

    async def test_framework_500_is_sent_before_telemetry_delivery(self):
        from starlette.applications import Starlette
        from starlette.routing import Route

        async def fail(request):
            raise RuntimeError("server error")

        app = Starlette(routes=[
            Route("/api/v1/chat/completions", fail, methods=["POST"]),
        ])
        messages = []

        async def send(message):
            self.post.assert_not_awaited()
            messages.append(message)

        with self.assertRaisesRegex(RuntimeError, "server error"):
            await self.run_request(app, send=send)
        self.assertEqual(messages[0]["status"], 500)
        self.assertEqual(messages[-1]["type"], "http.response.body")
        self.assertEqual(self.event()["output"]["outcome"], "error")

    async def test_failure_after_completed_body_preserves_delivered_success(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"{}"})
            raise RuntimeError("background failure")

        with self.assertRaises(RuntimeError):
            await self.run_request(app)
        self.assertTrue(self.event()["success"])

    async def test_disconnect_and_cancelled_stream(self):
        async def disconnected(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await receive()

        await self.run_request(disconnected, receive=AsyncMock(
            return_value={"type": "http.disconnect"}
        ))
        self.assertEqual(self.event()["output"]["errorType"], "client_disconnect")

        self.post.reset_mock()
        async def cancelled(scope, receive, send):
            raise asyncio.CancelledError()

        with patch.object(telemetry.time, "perf_counter", side_effect=[10, 10.125]):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_request(cancelled)
        self.assertEqual(self.event()["output"]["errorType"], "client_disconnect")

    async def test_incomplete_response_and_failed_send(self):
        async def incomplete(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})

        await self.run_request(incomplete)
        self.assertEqual(self.event()["output"]["errorType"], "incomplete_response")

        self.post.reset_mock()
        with patch.object(telemetry.time, "perf_counter", side_effect=[10, 10.125]):
            with self.assertRaises(OSError):
                await self.run_request(incomplete, send=AsyncMock(side_effect=OSError()))
        self.assertEqual(self.event()["output"]["outcome"], "error")

    async def test_skips_other_routes_methods_and_websockets(self):
        app = AsyncMock()
        for options in [{"path": "/api/health"}, {"method": "OPTIONS"},
                        {"scope_type": "websocket"}]:
            await self.run_request(app, **options)
        self.assertEqual(app.await_count, 3)
        self.post.assert_not_awaited()

    async def test_tracks_requests_with_trailing_slash(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"{}"})

        await self.run_request(app, path="/api/v1/chat/completions/")
        details = self.event()
        self.assertTrue(details["success"])

    async def test_tracks_requests_when_root_path_is_set(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"{}"})

        await self.run_request(
            app,
            path="/v1/chat/completions",
            root_path="/api",
        )
        details = self.event()
        self.assertTrue(details["success"])

    async def test_delivery_failures_are_logged_without_breaking_response(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"{}"})

        for result in [None, SimpleNamespace(status_code=503), ValueError("transport")]:
            with self.subTest(result=result):
                self.post.reset_mock()
                self.post.side_effect = result if isinstance(result, Exception) else None
                self.post.return_value = result
                with patch.object(telemetry.time, "perf_counter", side_effect=[10, 10.125]):
                    with self.assertLogs(telemetry.logger, level="WARNING"):
                        messages = await self.run_request(app)
                self.assertEqual(messages[-1]["body"], b"{}")


if __name__ == "__main__":
    unittest.main()
