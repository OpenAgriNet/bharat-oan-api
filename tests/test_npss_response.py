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
    async def test_token_request_matches_postman_collection(self):
        original_async_client = npss.httpx.AsyncClient
        original_username = npss.NPSS_USERNAME
        original_password = npss.NPSS_PASSWORD
        captured = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"token": "fake-token"}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, **kwargs):
                captured.update(kwargs)
                return FakeResponse()

        try:
            npss.NPSS_USERNAME = "collection-user"
            npss.NPSS_PASSWORD = "collection-password"
            npss.httpx.AsyncClient = lambda *args, **kwargs: FakeClient()

            token = await npss._get_npss_token()

            self.assertEqual(token, "fake-token")
            self.assertEqual(
                captured["json"],
                {"userName": "collection-user", "password": "collection-password"},
            )
            self.assertEqual(
                captured["headers"],
                {"accept": "text/plain", "Content-Type": "application/json"},
            )
        finally:
            npss.httpx.AsyncClient = original_async_client
            npss.NPSS_USERNAME = original_username
            npss.NPSS_PASSWORD = original_password

    async def test_npss_request_omits_unresolved_location_fields(self):
        original_async_client = npss.httpx.AsyncClient
        original_get_token = npss._get_cached_npss_token
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"pest": "test"}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, **kwargs):
                captured.update(kwargs)
                return FakeResponse()

        async def fake_get_token():
            return "fake-token"

        try:
            npss.httpx.AsyncClient = lambda *args, **kwargs: FakeClient()
            npss._get_cached_npss_token = fake_get_token

            await npss._call_npss_analyze(
                image_bytes=b"image",
                mimetype="image/jpeg",
                state_id="16",
                district_id="291",
                sub_district_id="2996",
                village_id=None,
                latitude=12.928,
                longitude=77.555,
            )

            self.assertEqual(
                captured["data"],
                {
                    "StateId": "16",
                    "DistrictId": "291",
                    "SubDistrictId": "2996",
                    "Latitude": "12.928",
                    "Longitude": "77.555",
                },
            )
            self.assertNotIn("VillageId", captured["data"])
        finally:
            npss.httpx.AsyncClient = original_async_client
            npss._get_cached_npss_token = original_get_token

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

    async def test_analyze_crop_image_skips_npss_when_complete_hierarchy_is_unavailable(self):
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

            deps = FarmerContext(
                query="Analyze image",
                lang_code="en",
                session_id="test",
                latitude=18.58,
                longitude=73.98,
            )
            ctx = SimpleNamespace(deps=deps)

            result = await npss.analyze_crop_image(ctx, "https://example.com/crop.jpg")

            self.assertIn("required village master location could not be verified", result)
            self.assertEqual(captured_request, {})
        finally:
            npss._download_image = original_download_image
            npss._call_npss_analyze = original_call_npss_analyze
            npss.cleanup_image = original_cleanup_image
            npss._get_cached_npss_token = original_get_token
            npss.resolve_npss_location_ids = original_resolve_location_ids


class TestNPSSGeocodeMapping(unittest.IsolatedAsyncioTestCase):
    def test_name_match_does_not_accept_a_shared_generic_word(self):
        rows = [{"id": 1, "name": "Bengaluru South"}]
        result = npss_geocodes._pick_best_row(rows, ["Unrelated South"], ("name",))
        self.assertIsNone(result)

    def test_name_match_does_not_accept_substring_inside_another_word(self):
        rows = [{"id": 1, "name": "Alur"}]
        result = npss_geocodes._pick_best_row(rows, ["Bangalore South"], ("name",))
        self.assertIsNone(result)

    def test_name_match_normalizes_only_formatting(self):
        rows = [{"id": 27, "name": "Dadra & Nagar Haveli"}]
        result = npss_geocodes._pick_best_row(rows, ["dadra and nagar haveli"], ("name",))
        self.assertIsNone(result)

        result = npss_geocodes._pick_best_row(rows, ["DADRA - NAGAR HAVELI"], ("name",))
        self.assertIs(result, rows[0])

    def test_name_match_removes_only_administrative_suffixes(self):
        rows = [{"id": 4287, "name": "Haveli"}]
        result = npss_geocodes._pick_best_row(rows, ["Haveli Subdistrict"], ("name",))
        self.assertIs(result, rows[0])

    async def test_master_api_background_lookup_matches_reverse_geocode_names(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows

        async def fake_reverse(_latitude: float, _longitude: float):
            return {
                "state": "Maharashtra",
                "county": "Haveli Subdistrict",
                "city": "Wagholi, Pune",
                "name": "Agarwal Business Hub",
                "osm_key": "building",
                "type": "house",
                "_nearby": [
                    {
                        "name": "Nearby Village",
                        "county": "Haveli Subdistrict",
                        "city": "Nearby Village",
                        "osm_key": "place",
                        "type": "city",
                    }
                ],
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
                return [
                    {"villageId": 556640, "villageName": "Wagholi"},
                    {"villageId": 556641, "villageName": "Nearby Village"},
                ]
            return []

        try:
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

    async def test_master_api_lookup_returns_only_verified_parent_hierarchy(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows

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
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(18.58, 73.98, bearer_token="token")

            self.assertEqual(
                result,
                {
                    "state_id": "27",
                },
            )
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch

    async def test_exact_village_match_resolves_canonical_bengaluru_hierarchy(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows

        async def fake_reverse(_latitude: float, _longitude: float):
            return {
                "state": "Karnataka",
                "county": "Bangalore South",
                "district": "Kathriguppe",
                "locality": "Banashankari",
                "name": "Basket ball court",
                "osm_key": "leisure",
                "type": "house",
            }

        async def fake_fetch(endpoint: str, *, bearer_token: str, params=None):
            if endpoint == "States":
                return [{"id": 16, "name": "Karnataka"}]
            if endpoint == "Districts":
                return [
                    {"id": 290, "name": "Bengaluru South"},
                    {"id": 291, "name": "Bengaluru Urban"},
                ]
            if endpoint == "SubDistricts" and params["districtId"] == "290":
                return [{"id": 3130, "name": "Channapatna"}]
            if endpoint == "SubDistricts" and params["districtId"] == "291":
                return [
                    {"id": 2993, "name": "Anekal"},
                    {"id": 2996, "name": "Bengaluru South"},
                ]
            if endpoint == "Vilages":
                if params["districtId"] == "291" and params["subDistrictId"] == "2993":
                    return [{"id": 532240, "name": "Kathriguppe"}]
                if params["districtId"] == "291" and params["subDistrictId"] == "2996":
                    return [{"id": 534896, "name": "Hosakerehalli"}]
                return []
            return []

        try:
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(12.928, 77.555, bearer_token="token")

            self.assertEqual(
                result,
                {
                    "state_id": "16",
                    "district_id": "291",
                    "sub_district_id": "2993",
                    "village_id": "532240",
                },
            )
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch

    async def test_master_api_lookup_returns_none_when_state_cannot_be_verified(self):
        original_reverse = npss_geocodes._reverse_geocode_properties
        original_fetch = npss_geocodes._fetch_master_rows

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
            npss_geocodes._reverse_geocode_properties = fake_reverse
            npss_geocodes._fetch_master_rows = fake_fetch

            result = await npss_geocodes.resolve_npss_location_ids(18.58, 73.98, bearer_token="token")

            self.assertIsNone(result)
        finally:
            npss_geocodes._reverse_geocode_properties = original_reverse
            npss_geocodes._fetch_master_rows = original_fetch


if __name__ == "__main__":
    unittest.main()
