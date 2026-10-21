import re
from pathlib import Path

PROMPT_DIR = Path("assets/prompts")
PROMPT_LANGS = sorted(p.stem.removeprefix("agrinet_") for p in PROMPT_DIR.glob("agrinet_*.md"))
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")


def test_every_prompt_language_is_wired_up():
    from agents.tools.terms import SUPPORTED_LANGS
    from app.services.npss_response import LABELS, LANGUAGE_SCRIPT_RANGES, SOURCE_LINES
    for lang in PROMPT_LANGS:
        assert lang in SUPPORTED_LANGS, lang
        assert lang in LABELS and lang in SOURCE_LINES, lang
        if lang != "en":
            assert lang in LANGUAGE_SCRIPT_RANGES, lang


def test_translated_prompts_keep_english_template_tokens():
    en_tokens = JINJA.findall((PROMPT_DIR / "agrinet_en.md").read_text(encoding="utf-8"))
    for lang in ("or", "pa", "mai"):
        tokens = JINJA.findall((PROMPT_DIR / f"agrinet_{lang}.md").read_text(encoding="utf-8"))
        # Capability includes point to each language's own verbatim fragments.
        normalized = [token.replace(f'agrinet_{lang}/', 'agrinet_en/') for token in tokens]
        assert normalized == en_tokens, lang


def test_keyword_language_codes_round_trip():
    from agents.tools.terms import TermPair
    pair = TermPair(**{"en": "Seed", "or": ["ବିହନ"], "as": "বীজ", "pa": "ਬੀਜ", "mai": ["बीया", "बीज"]})
    assert pair.get_terms("or") == ["ବିହନ"]
    assert pair.get_terms("as") == ["বীজ"]
    assert pair.get_terms("pa") == ["ਬੀਜ"]
    assert pair.get_term("mai") == "बीया"
