import re
from pathlib import Path

from agents.tools.terms import SUPPORTED_LANGS, TermPair
from app.services.npss_response import LABELS, LANGUAGE_SCRIPT_RANGES, SOURCE_LINES
from helpers.utils import get_prompt

PROMPT_DIR = Path("assets/prompts")
PROMPT_LANGS = sorted(p.stem.removeprefix("agrinet_") for p in PROMPT_DIR.glob("agrinet_*.md"))
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")


def test_every_prompt_language_is_wired_up():
    for lang in PROMPT_LANGS:
        assert lang in SUPPORTED_LANGS, lang
        assert lang in LABELS and lang in SOURCE_LINES, lang
        if lang != "en":
            assert lang in LANGUAGE_SCRIPT_RANGES, lang


def test_translated_prompts_keep_english_template_tokens():
    en_tokens = JINJA.findall((PROMPT_DIR / "agrinet_en.md").read_text(encoding="utf-8"))
    for lang in ("or", "pa", "mai"):
        tokens = JINJA.findall((PROMPT_DIR / f"agrinet_{lang}.md").read_text(encoding="utf-8"))
        assert tokens == en_tokens, lang


def test_every_language_prompt_renders():
    for lang in PROMPT_LANGS:
        assert get_prompt(f"agrinet_{lang}", context={"today_date": "x"}).strip(), lang


def test_keyword_language_codes_round_trip():
    pair = TermPair(**{"en": "Seed", "hi": "बीज", "transliteration": "beej", "or": "ବିହନ", "as": "বীজ", "pa": "ਬੀਜ", "mai": "बीया"})
    assert pair.get_term("or") == "ବିହନ"
    assert pair.get_term("as") == "বীজ"
    assert pair.get_term("pa") == "ਬੀਜ"
    assert pair.get_term("mai") == "बीया"
