"""Place lookup: the farmer's words to LGD codes."""

import json
from pathlib import Path

import pytest

from helpers.locations import _DATA_FILE, resolve_place


@pytest.mark.parametrize(
    "text, state, district",
    [
        ("Agra, Uttar Pradesh", 9, 118),
        ("agra", 9, 118),
        ("I farm near Agra", 9, 118),
        ("Uttar Pradesh", 9, None),
        ("UP", 9, None),
        ("U.P.", 9, None),
        ("उत्तर प्रदेश", 9, None),
        ("Bijapur, Karnataka", 29, 530),
        ("Umaria Madhya Pradesh", 23, 436),
        ("Tamil Nadu", 33, None),
    ],
)
def test_resolves_state_and_district(text, state, district):
    place = resolve_place(text)
    assert (place.state_code, place.district_code) == (state, district)


def test_unknown_places_do_not_resolve():
    assert not resolve_place("Pune").known
    assert not resolve_place("").known
    assert not resolve_place(None, "  ").known


def test_a_district_name_in_two_states_follows_the_named_state():
    place = resolve_place("Balrampur", "Uttar Pradesh")
    assert (place.state_code, place.district_code) == (9, 127)


def test_label_and_names_are_official_spellings():
    place = resolve_place("bijapur")
    assert place.label == "Vijayapura, Karnataka"


def test_two_letter_abbreviations_are_ignored_when_scanning_prose():
    assert not resolve_place("my crop is growing up well", allow_short=False).known
    assert resolve_place("UP", allow_short=True).state_code == 9


def test_whole_words_only():
    assert not resolve_place("Agrarian reform").known


def test_data_file_is_consistent():
    data = json.loads(Path(_DATA_FILE).read_text(encoding="utf-8"))
    state_codes = [s["code"] for s in data["states"]]
    assert len(state_codes) == len(set(state_codes))
    district_codes = [d["code"] for d in data["districts"]]
    assert len(district_codes) == len(set(district_codes))
    assert all(d["state"] in state_codes for d in data["districts"])
    # codes seen in the advisory webhook
    assert {5, 9, 13, 19, 23, 28, 29, 32, 33, 35} <= set(state_codes)
