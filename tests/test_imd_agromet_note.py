"""IMD agromet note added to weather and crop-advice tool results (no network, DB or Redis)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from agents.capability_policy import ToolPolicy
from agents.tools import imd_advisory

BULLETIN = {
    "districts": [{"lgd_district_code": 497, "name": "Nashik"}],
    "date": "2026-10-20",
    "valid_until": "2026-10-22",
    "advisories": [
        {"crop": "Maize", "advisory_eng": "..."},
        {"crop": "Onion", "advisory_eng": "..."},
        {"crop": None, "advisory_eng": "..."},
    ],
}
EMPTY = {"districts": [{"lgd_district_code": 497, "name": "Nashik"}], "date": None, "valid_until": None, "advisories": []}


class FakeCache:
    def __init__(self):
        self.data = {}

    async def get(self, key, namespace=None):
        return self.data.get((namespace, key))

    async def set(self, key, value, ttl=None, namespace=None):
        self.data[(namespace, key)] = value


def context(session_id="s1", allowed=None):
    policy = ToolPolicy() if allowed is None else ToolPolicy(allowed_tools=frozenset(allowed))
    return SimpleNamespace(deps=SimpleNamespace(
        session_id=session_id, latitude=19.9975, longitude=73.7898,
        capabilities=SimpleNamespace(policy=policy),
    ))


class AgrometNote(unittest.TestCase):
    def setUp(self):
        self.cache = FakeCache()
        self.lookup = BULLETIN
        patches = [
            mock.patch.object(imd_advisory, "cache", self.cache),
            mock.patch.object(imd_advisory, "IMD_DB_URL", "postgresql://test"),
            mock.patch.object(imd_advisory, "resolver_configured", return_value=True),
            mock.patch.object(imd_advisory, "_location_keys", mock.AsyncMock(return_value=["0123"])),
            mock.patch.object(imd_advisory, "_lookup_sync", side_effect=lambda keys: self.lookup),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def note(self, ctx, **kwargs):
        return asyncio.run(imd_advisory.agromet_note(ctx, **kwargs))

    def test_weather_lists_named_crops(self):
        text = self.note(context())
        self.assertIn("Nashik", text)
        self.assertIn("Maize, Onion", text)

    def test_crop_search_names_only_the_matched_crop(self):
        text = self.note(context(), query="maize fall armyworm control")
        self.assertIn("advisory for Maize", text)
        self.assertNotIn("Onion", text)

    def test_plural_crop_name_matches(self):
        self.assertIn("advisory for Onion", self.note(context(), query="onions storage rot"))

    def test_crop_not_covered_adds_nothing(self):
        self.assertEqual(self.note(context(), query="wheat rust symptoms"), "")

    def test_non_crop_search_adds_nothing(self):
        self.assertEqual(self.note(context(), query="buffalo mastitis treatment"), "")

    def test_no_current_advisory_adds_nothing(self):
        self.lookup = EMPTY
        self.assertEqual(self.note(context()), "")

    def test_only_once_per_conversation(self):
        self.assertTrue(self.note(context("s2")))
        self.assertEqual(self.note(context("s2")), "")
        self.assertEqual(self.note(context("s2"), query="maize sowing time"), "")
        self.assertTrue(self.note(context("s3")))

    def test_lookup_failure_adds_nothing(self):
        with mock.patch.object(imd_advisory, "_location_keys", mock.AsyncMock(side_effect=RuntimeError("down"))):
            self.assertEqual(self.note(context()), "")

    def test_missing_location_adds_nothing(self):
        ctx = context()
        ctx.deps.latitude = None
        self.assertEqual(self.note(ctx), "")

    def test_tool_not_allowed_adds_nothing(self):
        self.assertEqual(self.note(context(allowed={"weather_forecast"})), "")


if __name__ == "__main__":
    unittest.main()
