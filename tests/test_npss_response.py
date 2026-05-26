import unittest
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from agents.deps import FarmerContext
from app.services.npss_response import post_process_npss_response

ROOT = Path(__file__).resolve().parent.parent
NPSS_PATH = ROOT / "agents" / "tools" / "npss.py"
spec = importlib.util.spec_from_file_location("npss_tool_for_test", NPSS_PATH)
npss = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(npss)


class FakeTranslator:
    def __init__(self, source_lang: str, target_lang: str):
        self.source_lang = source_lang
        self.target_lang = target_lang

    def translate_texts(self, texts: list[str]) -> list[str]:
        translations = {
            "Pink bollworm": "गुलाबी सुंडी",
            "Cotton": "कपास",
            "insect": "कीट",
            "Larvae damage cotton bolls.": "लार्वा कपास के टिंडों को नुकसान पहुंचाते हैं।",
            "The image shows leaf spot disease.": "छवि में पत्ती धब्बा रोग दिख रहा है।",
        }
        return [translations.get(text, f"अनुवादित: {text}") for text in texts]


def fake_translator_factory(source_lang: str, target_lang: str) -> FakeTranslator:
    return FakeTranslator(source_lang, target_lang)


class TestNPSSResponsePostProcessing(unittest.TestCase):
    def test_non_npss_response_is_unchanged(self):
        response = "General crop answer."
        self.assertEqual(
            post_process_npss_response(response, "hi", npss_used=False),
            response,
        )

    def test_empty_npss_response_does_not_add_source(self):
        self.assertEqual(
            post_process_npss_response("", "en", npss_used=True),
            "",
        )

    def test_english_npss_response_gets_official_source_and_no_follow_up(self):
        response = (
            "**Pest:** Pink bollworm\n"
            "**Crop:** Cotton\n"
            "**Cause:** insect\n\n"
            "Larvae damage cotton bolls.\n\n"
            "Would you like spray advice?"
        )

        processed = post_process_npss_response(response, "en", npss_used=True)

        self.assertIn("**Source: NPSS**", processed)
        self.assertNotIn("https://npss.dac.gov.in/", processed)
        self.assertNotIn("Would you like spray advice?", processed)

    def test_hindi_npss_response_is_translated_and_source_is_localized(self):
        response = (
            "**Pest:** Pink bollworm\n"
            "**Crop:** Cotton\n"
            "**Cause:** insect\n\n"
            "Larvae damage cotton bolls.\n\n"
            "**Source:** NPSS Pest Advisory"
        )

        processed = post_process_npss_response(
            response,
            "hi",
            npss_used=True,
            translator_factory=fake_translator_factory,
        )

        self.assertIn("**कीट:** गुलाबी सुंडी", processed)
        self.assertIn("**फसल:** कपास", processed)
        self.assertIn("**कारण:** कीट", processed)
        self.assertIn("लार्वा कपास के टिंडों को नुकसान पहुंचाते हैं।", processed)
        self.assertIn("**स्रोत: NPSS**", processed)
        self.assertNotIn("NPSS Pest Advisory", processed)

    def test_other_language_path_uses_translator_for_body(self):
        response = "The image shows leaf spot disease."

        processed = post_process_npss_response(
            response,
            "hi",
            npss_used=True,
            translator_factory=fake_translator_factory,
        )

        self.assertIn("छवि में पत्ती धब्बा रोग दिख रहा है।", processed)
        self.assertNotIn("The image shows leaf spot disease.", processed)

    def test_existing_hindi_body_is_not_retranslated(self):
        response = (
            "**Pest:** Peach Leaf Curl\n"
            "**Crop:** peach\n"
            "**Cause:** fungi\n\n"
            "पत्तियां मोटी, मुड़ी हुई और लाल-बैंगनी रंग की दिखती हैं।"
        )

        processed = post_process_npss_response(
            response,
            "hi",
            npss_used=True,
            translator_factory=fake_translator_factory,
        )

        self.assertIn("पत्तियां मोटी, मुड़ी हुई और लाल-बैंगनी रंग की दिखती हैं।", processed)
        self.assertNotIn("अनुवादित: पत्तियां", processed)


class TestNPSSToolMetadata(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_crop_image_marks_context_with_source_metadata(self):
        original_download_image = npss._download_image
        original_call_npss_analyze = npss._call_npss_analyze
        original_cleanup_image = npss.cleanup_image
        original_get_location_ids = npss.get_npss_location_ids

        async def fake_download_image(_image_url: str):
            return b"\xff\xd8\xfffake-jpeg", "image/jpeg"

        async def fake_call_npss_analyze(**_kwargs):
            return {
                "pest": "Pink bollworm",
                "crop": "Cotton",
                "pathogenClass": "insect",
                "description": "Larvae damage cotton bolls.",
            }

        try:
            npss._download_image = fake_download_image
            npss._call_npss_analyze = fake_call_npss_analyze
            npss.cleanup_image = lambda *_args, **_kwargs: None
            npss.get_npss_location_ids = lambda *_args, **_kwargs: {
                "state_id": "1",
                "district_id": "2",
                "sub_district_id": "3",
                "village_id": "4",
            }

            deps = FarmerContext(query="Analyze image", lang_code="en", session_id="test")
            ctx = SimpleNamespace(deps=deps)

            result = await npss.analyze_crop_image(ctx, "https://example.com/crop.jpg")

            self.assertTrue(deps.npss_used)
            self.assertEqual(deps.npss_source_name, "National Pest Surveillance System (NPSS)")
            self.assertEqual(deps.npss_source_url, "https://npss.dac.gov.in/")
            self.assertEqual(deps.npss_raw_result["pest"], "Pink bollworm")
            self.assertIn("**Source:** NPSS", result)
        finally:
            npss._download_image = original_download_image
            npss._call_npss_analyze = original_call_npss_analyze
            npss.cleanup_image = original_cleanup_image
            npss.get_npss_location_ids = original_get_location_ids


if __name__ == "__main__":
    unittest.main()
