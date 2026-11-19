"""Sarvam's X-Language codes map onto bharat's language codes (Odia: od <-> or)."""
import pytest

from agents.skill_catalog import get_catalog
from app.voice.languages import (
    LANGUAGES,
    SUPPORTED_ISO_LANGUAGE_CODES,
    get_language,
    iso_language_code,
    parse_iso_language_header,
    sarvam_language_code,
)


def test_odia_uses_or_internally_and_od_for_sarvam():
    assert parse_iso_language_header("od") == "or"
    assert parse_iso_language_header(" OD ") == "or"
    assert iso_language_code("or") == "od"
    assert sarvam_language_code("or") == "od-IN"
    assert get_language("od").name == "Odia"


@pytest.mark.parametrize("code", sorted(SUPPORTED_ISO_LANGUAGE_CODES))
def test_every_sarvam_code_round_trips(code):
    internal = parse_iso_language_header(code)
    assert iso_language_code(internal) == code


def test_voice_profile_languages_match_the_registry():
    assert set(get_catalog().manifest.agents["voice"].languages) == set(LANGUAGES)


@pytest.mark.parametrize("value", ["", "or", "xx", "hi-IN", None])
def test_unknown_header_values_are_rejected(value):
    with pytest.raises(ValueError):
        parse_iso_language_header(value)


def test_every_language_has_spoken_fixed_messages():
    for spec in LANGUAGES.values():
        assert spec.recording_disclaimer and spec.hold_message and spec.delay_message
