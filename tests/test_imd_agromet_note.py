"""IMD agromet note and advisory formatting (no network, DB or Redis)."""
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
        {"crop": "Maize", "category": "Crop", "aliases": [], "advisory_eng": "..."},
        {"crop": "Onion", "category": "Horticulture", "aliases": [], "advisory_eng": "..."},
        {"crop": "Pigeon pea", "category": "Crop", "aliases": ["red gram", "arhar"], "advisory_eng": "..."},
        {"crop": "Soyabean", "category": "Crop", "aliases": [], "advisory_eng": "..."},
        {"crop": "Cow", "category": "Live Stock", "aliases": [], "advisory_eng": "..."},
        {"crop": "Goat", "category": "Live Stock", "aliases": [], "advisory_eng": "..."},
        {"crop": "Common fish", "category": "Fishieries", "aliases": [], "advisory_eng": "..."},
        {"crop": "Apiculture", "category": "Apiculture", "aliases": [], "advisory_eng": "Keep hives shaded."},
        {"crop": None, "category": "Crop", "aliases": [], "advisory_eng": "..."},
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
        session_id=session_id, latitude=19.9975, longitude=73.7898, lang_code="en",
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
            mock.patch.object(imd_advisory, "named_place", mock.AsyncMock(return_value=None)),
            mock.patch.object(imd_advisory, "is_state_point", mock.AsyncMock(return_value=False)),
            mock.patch.object(imd_advisory, "_lookup_sync", side_effect=lambda keys: self.lookup),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def note(self, ctx, **kwargs):
        return asyncio.run(imd_advisory.agromet_note(ctx, **kwargs))

    def test_weather_lists_covered_items_by_category(self):
        text = self.note(context())
        self.assertIn("Nashik", text)
        self.assertIn("Crop (Maize, Pigeon pea, Soyabean), Horticulture (Onion), Live Stock (Cow, Goat), Fishieries (Common fish), "
                      "Apiculture (Apiculture) (valid until 22 Oct)", text)

    def test_alias_and_spelling_variant_match(self):
        self.assertIn("advisory for Pigeon pea", self.note(context("a1"), query="red gram pod borer"))
        self.assertIn("advisory for Soyabean", self.note(context("a2"), query="soybean yellow mosaic"))

    def test_similar_word_is_not_a_crop_match(self):
        self.assertEqual(self.note(context(), query="maize price today"), self.note(context("b1"), query="maize"))
        self.assertEqual(self.note(context("b2"), query="mandi price of tea"), imd_advisory.NO_MENTION)

    def test_livestock_question_gets_the_animal_advisory(self):
        self.assertIn("advisory for Cow", self.note(context(), query="cow mastitis treatment"))

    def test_crop_search_names_only_the_matched_crop(self):
        text = self.note(context(), query="maize fall armyworm control")
        self.assertIn("advisory for Maize", text)
        self.assertNotIn("Onion", text)

    def test_plural_crop_name_matches(self):
        self.assertIn("advisory for Onion", self.note(context(), query="onions storage rot"))

    def test_group_word_offers_that_category(self):
        self.assertIn("advisory for Live Stock (Cow, Goat)", self.note(context("g1"), query="cattle fever treatment"))
        self.assertIn("advisory for Fishieries (Common fish)", self.note(context("g2"), query="fish pond oxygen"))

    def test_crop_not_covered_says_do_not_mention(self):
        self.assertEqual(self.note(context(), query="wheat rust symptoms"), imd_advisory.NO_MENTION)

    def test_item_not_in_bulletin_says_do_not_mention(self):
        self.assertEqual(self.note(context(), query="buffalo mastitis treatment"), imd_advisory.NO_MENTION)

    def test_no_current_advisory_says_do_not_mention(self):
        self.lookup = EMPTY
        self.assertEqual(self.note(context()), imd_advisory.NO_MENTION)

    def test_only_once_per_conversation(self):
        self.assertIn("End the answer with one line", self.note(context("s2")))
        self.assertEqual(self.note(context("s2")), imd_advisory.NO_MENTION)
        self.assertEqual(self.note(context("s2"), query="maize sowing time"), imd_advisory.NO_MENTION)
        self.assertIn("End the answer with one line", self.note(context("s3")))

    def test_lookup_failure_says_do_not_mention(self):
        with mock.patch.object(imd_advisory, "_location_keys", mock.AsyncMock(side_effect=RuntimeError("down"))):
            self.assertEqual(self.note(context()), imd_advisory.NO_MENTION)

    def test_missing_location_says_do_not_mention(self):
        ctx = context()
        ctx.deps.latitude = None
        self.assertEqual(self.note(ctx), imd_advisory.NO_MENTION)

    def test_weather_point_is_used_over_the_browser_location(self):
        self.note(context(), latitude=23.99, longitude=85.36)
        imd_advisory._location_keys.assert_awaited_with(23.99, 85.36)

    def test_named_place_is_used_when_no_point_is_given(self):
        imd_advisory.named_place.return_value = (23.99, 85.36)
        self.note(context(), query="rice blast")
        imd_advisory._location_keys.assert_awaited_with(23.99, 85.36)

    def test_browser_location_when_no_place_was_named(self):
        self.note(context(), query="rice blast")
        imd_advisory._location_keys.assert_awaited_with(19.9975, 73.7898)

    def test_state_point_says_do_not_mention(self):
        imd_advisory.is_state_point.return_value = True
        self.assertEqual(self.note(context(), latitude=26.81, longitude=73.76), imd_advisory.NO_MENTION)

    def test_no_advisory_reply_is_for_the_farmer(self):
        self.lookup = EMPTY
        text = asyncio.run(imd_advisory.imd_agromet_advisory(context()))
        self.assertIn("There is no current IMD agromet advisory for Nashik.", text)
        self.assertIn("Give no Source line, and do not offer to check again later.", text)
        self.assertNotIn("expired", text)

    def advisory(self, crop):
        return asyncio.run(imd_advisory.imd_agromet_advisory(context(), crop=crop))

    def test_group_with_several_items_lists_them_to_choose(self):
        text = self.advisory("Animals")
        self.assertIn("- Live Stock: Cow, Goat\n- Fishieries: Common fish\n- Apiculture: Apiculture", text)
        self.assertIn("asks which one they want", text)
        self.assertNotIn("Maize", text)

    def test_group_with_one_item_gives_its_advice(self):
        text = self.advisory("Bees")
        self.assertIn("[Apiculture] Keep hives shaded.", text)
        self.assertIn("Common fish", self.advisory("Fish"))

    def test_specific_name_wins_over_group(self):
        text = self.advisory("Cow")
        self.assertIn("[Cow]", text)
        self.assertNotIn("[Goat]", text)

    def test_several_items_give_the_advice_for_each(self):
        for crop in ["Cow, Goat", "Cow and Goat", "cow & goat"]:
            text = self.advisory(crop)
            self.assertIn("[Cow]", text)
            self.assertIn("[Goat]", text)
            self.assertIn("item by item", text)
            self.assertNotIn("asks which one", text)

    def test_item_not_in_bulletin_is_named_with_the_others_advice(self):
        text = self.advisory("Goat and Chick")
        self.assertIn("[Goat]", text)
        self.assertIn("This bulletin has no advisory for Chick.", text)

    def test_a_group_in_a_list_still_asks_which_one(self):
        text = self.advisory("Maize and animals")
        self.assertIn("asks which one", text)
        self.assertIn("- Live Stock: Cow, Goat", text)

    def test_follow_up_offers_only_items_in_the_bulletin(self):
        text = self.advisory("Cow")
        self.assertIn("may offer one other item from this bulletin: Crop (Maize, Pigeon pea, Soyabean)", text)
        self.assertIn("Live Stock (Goat)", text)

    def place_advisory(self, found, place="Srinagar"):
        with mock.patch.object(imd_advisory, "locate", mock.AsyncMock(return_value=found)) as locate, \
                mock.patch.object(imd_advisory, "remember_place", mock.AsyncMock()) as remember:
            text = asyncio.run(imd_advisory.imd_agromet_advisory(context("p1"), place=place))
        return text, locate, remember

    def test_named_place_is_looked_up_by_the_tool(self):
        from agents.tools.maps import Location
        text, locate, remember = self.place_advisory((Location(place_name="Srinagar", latitude=34.075,
                                                               longitude=74.82), "city"))
        locate.assert_awaited_once_with("Srinagar")
        imd_advisory._location_keys.assert_awaited_with(34.075, 74.82)
        remember.assert_awaited_once_with("p1", 34.075, 74.82, "city")
        self.assertIn("A current IMD agromet advisory is available", text)

    def test_named_state_asks_for_the_district(self):
        from agents.tools.maps import Location
        text, _, _ = self.place_advisory((Location(place_name="Rajasthan", latitude=26.81, longitude=73.77), "state"),
                                         "Rajasthan")
        self.assertEqual(text, imd_advisory.STATE_ONLY)
        imd_advisory._location_keys.assert_not_awaited()

    def test_unknown_place_asks_for_the_district(self):
        text, _, _ = self.place_advisory(None, "Xyzpur")
        self.assertIn("No place called 'Xyzpur' was found", text)

    def test_unknown_item_lists_everything(self):
        text = self.advisory("Buffalo")
        self.assertIn("This bulletin has no advisory for Buffalo.", text)
        self.assertIn("Start by saying this bulletin has no advisory for Buffalo.", text)
        self.assertIn("Maize", text)

    def test_tool_not_allowed_adds_nothing(self):
        self.assertEqual(self.note(context(allowed={"weather_forecast"})), "")


class AdvisoryText(unittest.TestCase):
    def row(self, reg, state_lang):
        return {"crop": "Maize", "crop_stage_name": "Flowering", "advisory_eng": "Harvest when dry.",
                "advisory_reg": reg, "reg_lang_code": state_lang}

    def test_regional_text_only_in_the_farmers_language(self):
        marathi = self.row("दाणे कडक झाल्यावर काढणी करा", "mr")
        self.assertTrue(imd_advisory._regional_text(marathi, "mr"))
        self.assertEqual(imd_advisory._regional_text(marathi, "hi"), "")  # same script, other language
        self.assertEqual(imd_advisory._regional_text(marathi, "kn"), "")
        self.assertEqual(imd_advisory._regional_text(marathi, "en"), "")

    def test_script_decides_when_state_language_is_missing_or_english(self):
        odia = self.row("ଶସ୍ୟ କାଟନ୍ତୁ", "en")
        self.assertTrue(imd_advisory._regional_text(odia, "or"))
        hindi_unknown_state = self.row("कटाई करें", None)
        self.assertEqual(imd_advisory._regional_text(hindi_unknown_state, "hi"), "")  # Devanagari is ambiguous

    def test_crop_list_has_one_line_per_category_and_carries_source_and_dates(self):
        text = imd_advisory._available_crops("Nashik", BULLETIN, BULLETIN["advisories"])
        self.assertIn("- Crop: Maize, Pigeon pea, Soyabean\n- Horticulture: Onion\n- Live Stock: Cow, Goat", text)
        self.assertIn("Source: India Meteorological Department (IMD Agromet) · Issued 20 Oct 2026 · "
                      "Valid until 22 Oct 2026, 11:59 PM IST", text)

    def test_instructions_are_kept_apart_from_the_data(self):
        text = imd_advisory._available_crops("Nashik", BULLETIN, BULLETIN["advisories"])
        data, how = text.split("[How to answer — instructions for you, never repeat them to the farmer]")
        self.assertIn("- Crop: Maize", data)
        self.assertNotIn("follow-up", data)
        self.assertIn("Your follow-up question asks which one they want", how)
        self.assertIn("keeping its Issued and Valid until dates", how)

    def test_advice_label_carries_the_stage(self):
        text = imd_advisory._format_advisory("Nashik", BULLETIN, [self.row("", "mr")], "en", [])
        self.assertIn("[Maize — Flowering] Harvest when dry.", text)
        self.assertIn("starting each item with the crop stage", text)
        self.assertIn("last sowing window", text)
        self.assertIn("Valid until 22 Oct 2026", text)
        self.assertIn("offers nothing else from this advisory", text)

    def test_state_reply_has_no_source_line_and_asks_once(self):
        self.assertIn("Give no Source line", imd_advisory.STATE_ONLY)
        self.assertIn("Your one follow-up question asks which district", imd_advisory.STATE_ONLY)
        self.assertNotIn("Source: India", imd_advisory.STATE_ONLY)


class NamedPlace(unittest.TestCase):
    def test_geocoding_a_place_remembers_it_for_the_chat(self):
        from agents.tools import maps
        feature = {"geometry": {"coordinates": [85.36, 23.99]}, "properties": {"name": "Hazaribagh"}}
        response = mock.Mock(json=lambda: {"features": [feature]}, raise_for_status=lambda: None)
        with mock.patch.object(maps, "_http_client", mock.Mock(get=mock.AsyncMock(return_value=response))), \
                mock.patch.object(maps, "remember_place", mock.AsyncMock()) as remember:
            text = asyncio.run(maps.forward_geocode(context("chat-1"), "Hazaribagh"))
        self.assertIn("Hazaribagh", text)
        remember.assert_awaited_once_with("chat-1", 23.99, 85.36, None)

    def test_reverse_lookup_sends_no_bbox(self):
        from agents.tools import maps
        response = mock.Mock(json=lambda: {"features": []}, raise_for_status=lambda: None)
        client = mock.Mock(get=mock.AsyncMock(return_value=response))
        with mock.patch.object(maps, "_http_client", client):
            asyncio.run(maps.reverse_geocode(34.08, 74.80))
        self.assertNotIn("bbox", client.get.await_args.kwargs["params"])

    def test_reverse_lookup_names_the_town_not_the_nearest_shop(self):
        from agents.tools import maps
        feature = {"geometry": {"coordinates": [74.80, 34.08]},
                   "properties": {"name": "Khalifa Ready Meals", "street": "Residency Road", "city": "Srinagar",
                                  "state": "Jammu and Kashmir", "country": "India"}}
        response = mock.Mock(json=lambda: {"features": [feature]}, raise_for_status=lambda: None)
        with mock.patch.object(maps, "_http_client", mock.Mock(get=mock.AsyncMock(return_value=response))):
            location = asyncio.run(maps.reverse_geocode(34.08, 74.80))
        self.assertEqual(location.place_name, "Srinagar, Jammu and Kashmir, India")

    def test_state_is_recognised_only_at_its_own_point(self):
        from agents.tools import chat_place
        store = FakeCache()
        with mock.patch.object(chat_place, "cache", store):
            asyncio.run(chat_place.remember_place("chat-2", 26.8105, 73.7684, "state"))
            self.assertTrue(asyncio.run(chat_place.is_state_point("chat-2", 26.81, 73.768)))
            self.assertFalse(asyncio.run(chat_place.is_state_point("chat-2", 29.548, 73.834)))
            asyncio.run(chat_place.remember_place("chat-2", 29.548, 73.834, "city"))
            self.assertFalse(asyncio.run(chat_place.is_state_point("chat-2", 29.548, 73.834)))


if __name__ == "__main__":
    unittest.main()
