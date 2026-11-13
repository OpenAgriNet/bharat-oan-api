"""Formatting and script policy, without model services or application startup."""
import re
from pathlib import Path

import pytest

from app.services.guided_decoding import guided_decoding_settings

WORDS = {
    "kn": "ನಮಸ್ಕಾರ", "ta": "வணக்கம்", "ml": "നമസ്കാരം", "te": "నమస్కారం",
    "bn": "স্বাগতম", "as": "অসমীয়া", "gu": "નમસ્તે",
}
MARKDOWN = (Path(__file__).parent / "fixtures/guided_decoding_markdown.md").read_text()


@pytest.mark.parametrize("language", WORDS)
def test_markdown_symbols_and_target_script_are_allowed(language):
    pattern = guided_decoding_settings(language, "vllm")["extra_body"]["structured_outputs"]["regex"]
    assert re.fullmatch(pattern, MARKDOWN.format(word=WORDS[language], lang=language))
    assert re.fullmatch(pattern, "\t\n\r" + "".join(map(chr, range(32, 127))))
    assert re.fullmatch(pattern, "English identifiers and entire English sentences remain allowed.")
    assert pattern.startswith("^[") and not pattern.startswith("^[^")
    for forbidden in ["\x00", "\x7f", "中文", "हिन्दी", "العربية", "Русский", "한글"]:
        assert re.fullmatch(pattern, forbidden) is None
    for other, word in WORDS.items():
        if other != language and {language, other} != {"bn", "as"}:
            assert re.fullmatch(pattern, word) is None


@pytest.mark.parametrize("kind", ["openai", "azure-openai", "bharat_ai_grid", "", "unknown"])
@pytest.mark.parametrize("language", WORDS)
def test_other_providers_never_receive_vllm_settings(language, kind):
    assert guided_decoding_settings(language, kind) is None


@pytest.mark.parametrize("language", [None, "", "en", "hi", "or", "pa", "mai", "unknown"])
def test_ungated_languages_remain_unchanged(language):
    assert guided_decoding_settings(language, "vllm") is None


def test_case_and_shared_bengali_assamese_script():
    assert guided_decoding_settings("KN", "vllm") == guided_decoding_settings("kn", "vllm")
    assert guided_decoding_settings("as", "vllm") == guided_decoding_settings("bn", "vllm")
