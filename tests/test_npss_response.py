import unittest
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

from agents.deps import FarmerContext
from app.core import npss_geocodes
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
    async def test_download_image_resolves_raw_uuid_to_internal_image_url(self):
        original_async_client = npss.httpx.AsyncClient
        original_base_url = os.environ.get("BASE_URL")
        captured = {}

        class FakeResponse:
            content = b"\xff\xd8\xfffake-jpeg"
            headers = {"content-type": "image/jpeg"}

            def raise_for_status(self):
                return None

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **_kwargs):
                captured["url"] = url
                return FakeResponse()

        try:
            os.environ["BASE_URL"] = "https://api.example.test"
            npss.httpx.AsyncClient = lambda *args, **kwargs: FakeClient()

            data, mimetype = await npss._download_image("9b5c8815-772d-46ec-88bf-bbfbd8e6a96e")

            self.assertEqual(data, b"\xff\xd8\xfffake-jpeg")
            self.assertEqual(mimetype, "image/jpeg")
            self.assertEqual(
                captured["url"],
                "https://api.example.test/api/image/9b5c8815-772d-46ec-88bf-bbfbd8e6a96e",
            )
        finally:
            npss.httpx.AsyncClient = original_async_client
            if original_base_url is None:
                os.environ.pop("BASE_URL", None)
            else:
                os.environ["BASE_URL"] = original_base_url

    async def test_analyze_crop_image_marks_context_with_source_metadata(self):
        original_download_image = npss._download_image
        original_call_npss_analyze = npss._call_npss_analyze
        original_cleanup_image = npss.cleanup_image
        original_get_token = npss._get_cached_npss_token
        original_resolve_location_ids = npss.resolve_npss_location_ids

        async def fake_download_image(_image_url: str):
            return b"\xff\xd8\xfffake-jpeg", "image/jpeg"

        async def fake_get_token():
            return "fake-token"

        async def fake_resolve_location_ids(*_args, **_kwargs):
            return {
                "state_id": "1",
                "district_id": "2",
                "sub_district_id": "3",
                "village_id": "4",
            }

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
            npss._get_cached_npss_token = fake_get_token
            npss.resolve_npss_location_ids = fake_resolve_location_ids

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
            npss._get_cached_npss_token = original_get_token
            npss.resolve_npss_location_ids = original_resolve_location_ids

    async def test_analyze_crop_image_uses_default_ids_when_resolution_fails(self):
        original_download_image = npss._download_image
        original_call_npss_analyze = npss._call_npss_analyze
        original_cleanup_image = npss.cleanup_image
        original_get_token = npss._get_cached_npss_token
        original_resolve_location_ids = npss.resolve_npss_location_ids
        captured_request = {}

        async def fake_download_image(_image_url: str):
            return b"\xff\xd8\xfffake-jpeg", "image/jpeg"

        async def fake_get_token():
            return "fake-token"

        async def fake_resolve_location_ids(*_args, **_kwargs):
            return None

        async def fake_call_npss_analyze(**kwargs):
            captured_request.update(kwargs)
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
            npss._get_cached_npss_token = fake_get_token
            npss.resolve_npss_location_ids = fake_resolve_location_ids

            deps = FarmerContext(query="Analyze image", lang_code="en", session_id="test")
            ctx = SimpleNamespace(deps=deps)

            result = await npss.analyze_crop_image(ctx, "https://example.com/crop.jpg", latitude=18.58, longitude=73.98)

            self.assertIn("**Source:** NPSS", result)
            self.assertEqual(captured_request["state_id"], "1")
            self.assertEqual(captured_request["district_id"], "1")
            self.assertEqual(captured_request["sub_district_id"], "1")
            self.assertEqual(captured_request["village_id"], "1")
        finally:
            npss._download_image = original_download_image
            npss._call_npss_analyze = original_call_npss_analyze
            npss.cleanup_image = original_cleanup_image
            npss._get_cached_npss_token = original_get_token
            npss.resolve_npss_location_ids = original_resolve_location_ids


class TestNPSSGeocodeMapping(unittest.IsolatedAsyncioTestCase):
    def test_lookup_miss_does_not_default_to_location_one(self):
        original_map = npss_geocodes._npss_geocode_map
        original_allow_default = npss_geocodes.ALLOW_DEFAULT_LOCATION

        try:
            npss_geocodes._npss_geocode_map = {}
            npss_geocodes.ALLOW_DEFAULT_LOCATION = False

            self.assertIsNone(npss_geocodes.get_npss_location_ids(28.6, 77.2))
            self.assertIsNone(npss_geocodes.get_npss_location_ids(None, None))
        finally:
            npss_geocodes._npss_geocode_map = original_map
            npss_geocodes.ALLOW_DEFAULT_LOCATION = original_allow_default

    async def test_master_api_background_lookup_matches_reverse_geocode_names(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows
        original_map = npss_geocodes._npss_geocode_map
        original_allow_default = npss_geocodes.ALLOW_DEFAULT_LOCATION

        async def fake_reverse(_latitude: float, _longitude: float):
            return {
                "state": "Maharashtra",
                "county": "Pune",
                "city": "Haveli",
                "name": "Wagholi",
            }

        async def fake_fetch(endpoint: str, *, bearer_token: str, params=None):
            self.assertEqual(bearer_token, "token")
            if endpoint == "States":
                return [{"stateId": 27, "stateName": "Maharashtra"}]
            if endpoint == "Districts":
                self.assertEqual(params, {"stateId": "27"})
                return [{"districtId": 521, "districtName": "Pune"}]
            if endpoint == "SubDistricts":
                self.assertEqual(params, {"stateId": "27", "districtId": "521"})
                return [{"subDistrictId": 4287, "subDistrictName": "Haveli"}]
            if endpoint == "Vilages":
                self.assertEqual(
                    params,
                    {"stateId": "27", "districtId": "521", "subDistrictId": "4287"},
                )
                return [{"villageId": 556640, "villageName": "Wagholi"}]
            return []

        try:
            npss_geocodes._npss_geocode_map = {}
            npss_geocodes.ALLOW_DEFAULT_LOCATION = False
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(18.58, 73.98, bearer_token="token")

            self.assertEqual(
                result,
                {
                    "state_id": "27",
                    "district_id": "521",
                    "sub_district_id": "4287",
                    "village_id": "556640",
                },
            )
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch
            npss_geocodes._npss_geocode_map = original_map
            npss_geocodes.ALLOW_DEFAULT_LOCATION = original_allow_default

    async def test_master_api_lookup_degrades_within_matched_hierarchy(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows
        original_map = npss_geocodes._npss_geocode_map
        original_allow_default = npss_geocodes.ALLOW_DEFAULT_LOCATION

        async def fake_reverse(_latitude: float, _longitude: float):
            return {
                "state": "Maharashtra",
                "county": "Pune",
                "city": "Unmatched Taluka",
                "name": "Unmatched Village",
            }

        async def fake_fetch(endpoint: str, *, bearer_token: str, params=None):
            if endpoint == "States":
                return [{"stateId": 27, "stateName": "Maharashtra"}]
            if endpoint == "Districts":
                return [{"districtId": 521, "districtName": "Pune"}]
            if endpoint == "SubDistricts":
                return [
                    {"subDistrictId": 5002, "subDistrictName": "B"},
                    {"subDistrictId": 5001, "subDistrictName": "A"},
                ]
            if endpoint == "Vilages":
                return [
                    {"villageId": 9002, "villageName": "Y"},
                    {"villageId": 9001, "villageName": "X"},
                ]
            return []

        try:
            npss_geocodes._npss_geocode_map = {}
            npss_geocodes.ALLOW_DEFAULT_LOCATION = False
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(18.58, 73.98, bearer_token="token")

            self.assertEqual(
                result,
                {
                    "state_id": "27",
                    "district_id": "521",
                    "sub_district_id": "5001",
                    "village_id": "9001",
                },
            )
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch
            npss_geocodes._npss_geocode_map = original_map
            npss_geocodes.ALLOW_DEFAULT_LOCATION = original_allow_default

    async def test_master_api_lookup_degrades_when_state_and_district_names_do_not_match(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows
        original_map = npss_geocodes._npss_geocode_map
        original_allow_default = npss_geocodes.ALLOW_DEFAULT_LOCATION

        async def fake_reverse(_latitude: float, _longitude: float):
            return {
                "state": "Unmatched State",
                "county": "Unmatched District",
                "city": "Unmatched Taluka",
                "name": "Unmatched Village",
            }

        async def fake_fetch(endpoint: str, *, bearer_token: str, params=None):
            if endpoint == "States":
                return [
                    {"stateId": 29, "stateName": "B"},
                    {"stateId": 27, "stateName": "A"},
                ]
            if endpoint == "Districts":
                self.assertEqual(params, {"stateId": "27"})
                return [
                    {"districtId": 522, "districtName": "B"},
                    {"districtId": 521, "districtName": "A"},
                ]
            if endpoint == "SubDistricts":
                self.assertEqual(params, {"stateId": "27", "districtId": "521"})
                return [{"subDistrictId": 5001, "subDistrictName": "A"}]
            if endpoint == "Vilages":
                self.assertEqual(
                    params,
                    {"stateId": "27", "districtId": "521", "subDistrictId": "5001"},
                )
                return [{"villageId": 9001, "villageName": "X"}]
            return []

        try:
            npss_geocodes._npss_geocode_map = {}
            npss_geocodes.ALLOW_DEFAULT_LOCATION = False
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(18.58, 73.98, bearer_token="token")

            self.assertEqual(
                result,
                {
                    "state_id": "27",
                    "district_id": "521",
                    "sub_district_id": "5001",
                    "village_id": "9001",
                },
            )
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch
            npss_geocodes._npss_geocode_map = original_map
            npss_geocodes.ALLOW_DEFAULT_LOCATION = original_allow_default


if __name__ == "__main__":
    unittest.main()
