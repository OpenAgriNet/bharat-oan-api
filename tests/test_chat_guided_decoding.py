import re

from app.services.chat import _guided_decoding_settings

VLLM_ROUTE = "gemma_vllm"
AZURE_ROUTE = "azure_gpt41"


def test_hindi_english_get_no_gate():
    assert _guided_decoding_settings("hi", VLLM_ROUTE) is None
    assert _guided_decoding_settings("en", VLLM_ROUTE) is None
    assert _guided_decoding_settings(None, VLLM_ROUTE) is None


def test_non_vllm_route_gets_no_gate_even_for_a_gated_language():
    # config/models.yaml routes agrinet 50/50 between gemma_vllm and
    # azure_gpt41 — structured_outputs.regex is vLLM/xgrammar-only, so a
    # request that lands on Azure must never carry it.
    assert _guided_decoding_settings("kn", AZURE_ROUTE) is None


def test_kannada_gate_allows_own_script_ascii_and_danda():
    settings = _guided_decoding_settings("kn", VLLM_ROUTE)
    pattern = settings["extra_body"]["structured_outputs"]["regex"]

    assert re.fullmatch(pattern, "ನಮಸ್ಕಾರ") is not None
    assert re.fullmatch(pattern, "25°C ರಲ್ಲಿ, PM-KISAN!") is not None
    assert re.fullmatch(pattern, "ಮಳೆ। ಗಾಳಿ॥") is not None  # shared danda/double-danda


def test_kannada_gate_blocks_other_indic_and_cjk_scripts():
    settings = _guided_decoding_settings("kn", VLLM_ROUTE)
    pattern = settings["extra_body"]["structured_outputs"]["regex"]

    assert re.fullmatch(pattern, "यह हिंदी है") is None  # Devanagari
    assert re.fullmatch(pattern, "இது தமிழ்") is None  # Tamil
    assert re.fullmatch(pattern, "这是中文") is None  # CJK


def test_english_always_allowed_regardless_of_target_language():
    # ASCII isn't part of any script's exclusive range, so it's never in any
    # target's blocked set — confirm that explicitly for every gated language,
    # not just Kannada, since it's a hard requirement (code-switching, brand
    # names, tool-call JSON keys all need to survive regardless of target).
    for lang in ["kn", "ta", "ml", "te", "bn", "as", "gu"]:
        pattern = _guided_decoding_settings(lang, VLLM_ROUTE)["extra_body"]["structured_outputs"]["regex"]
        assert re.fullmatch(pattern, "PM-KISAN scheme apply karo, http://example.com!") is not None, lang


def test_assamese_reuses_bengali_script_range():
    kn = _guided_decoding_settings("kn", VLLM_ROUTE)["extra_body"]["structured_outputs"]["regex"]
    as_ = _guided_decoding_settings("as", VLLM_ROUTE)["extra_body"]["structured_outputs"]["regex"]

    assert re.fullmatch(as_, "এইটো অসমীয়া") is not None
    assert re.fullmatch(kn, "এইটো অসমীয়া") is None


def test_kannada_gate_allows_symbols_missed_by_142s_hand_picked_set():
    # #142's hand-picked punctuation set (reverted by #167) missed °, …, ×, ‰,
    # and never considered emoji at all. This design covers them for free via
    # Unicode's Common/Inherited script categories, not a hand-maintained list.
    settings = _guided_decoding_settings("kn", VLLM_ROUTE)
    pattern = settings["extra_body"]["structured_outputs"]["regex"]

    assert re.fullmatch(pattern, "25°C, ಸುಮಾರು 5% ಬೆಳೆ ನಷ್ಟ…") is not None
    assert re.fullmatch(pattern, "3×4 = 12") is not None
    assert re.fullmatch(pattern, "😀🌾🚜") is not None


def test_this_is_an_allowlist_not_a_denylist():
    # Explicit regression guard for the xgrammar bug (mlc-ai/xgrammar#848):
    # a NEGATED class over non-ASCII silently fails to enforce. This design
    # must always compile to a positive `[...]*` class, never `[^...]*`.
    pattern = _guided_decoding_settings("kn", VLLM_ROUTE)["extra_body"]["structured_outputs"]["regex"]
    assert pattern.startswith("^[") and not pattern.startswith("^[^")


if __name__ == "__main__":
    test_hindi_english_get_no_gate()
    test_non_vllm_route_gets_no_gate_even_for_a_gated_language()
    test_kannada_gate_allows_own_script_ascii_and_danda()
    test_kannada_gate_blocks_other_indic_and_cjk_scripts()
    test_english_always_allowed_regardless_of_target_language()
    test_assamese_reuses_bengali_script_range()
    test_kannada_gate_allows_symbols_missed_by_142s_hand_picked_set()
    test_this_is_an_allowlist_not_a_denylist()
    print("ok")
