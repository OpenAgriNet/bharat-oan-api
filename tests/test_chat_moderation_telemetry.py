import unittest

from fastapi import BackgroundTasks

from agents.moderation import QueryModerationResult
from app.services.chat import _enqueue_moderation_telemetry
from app.tasks.telemetry import send_telemetry


class ChatModerationTelemetryTests(unittest.TestCase):
    def test_enqueue_moderation_telemetry_adds_background_task(self):
        background_tasks = BackgroundTasks()
        moderation_data = QueryModerationResult(
            category="valid_agricultural",
            action="Allow the request.",
        )

        _enqueue_moderation_telemetry(
            background_tasks=background_tasks,
            query="How do I control pests in cotton?",
            session_id="session-123",
            user_id="user-123",
            moderation_data=moderation_data,
        )

        self.assertEqual(len(background_tasks.tasks), 1)

        task = background_tasks.tasks[0]
        self.assertIs(task.func, send_telemetry)

        telemetry_data = task.args[0]
        event = telemetry_data["events"][0]
        questions_details = event["edata"]["eks"]["target"]["questionsDetails"]

        self.assertEqual(event["eid"], "OE_MODERATION")
        self.assertEqual(event["uid"], "user-123")
        self.assertEqual(event["sid"], "session-123")
        self.assertEqual(event["edata"]["eks"]["type"], "CHAT_QUERY")
        self.assertEqual(questions_details["questionText"], "How do I control pests in cotton?")
        self.assertEqual(questions_details["contentId"], "session-123")
        self.assertEqual(questions_details["contentType"], "chat_query")
        self.assertEqual(questions_details["moderationService"], "moderation_agent")
        self.assertFalse(questions_details["flagged"])
        self.assertEqual(questions_details["category"], "valid_agricultural")
        self.assertEqual(questions_details["action"], "Allow the request.")


if __name__ == "__main__":
    unittest.main()
