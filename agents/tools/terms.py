import json
import re
from pydantic import BaseModel, Field, field_validator
from rapidfuzz import process

# Load term pairs from JSON file with UTF-8 encoding
term_pairs = json.load(open('assets/glossary_terms.json', 'r', encoding='utf-8'))

# ISO 639 codes. Note: voice-oan-api uses "od" for Odia; this repo uses the ISO code "or".
SUPPORTED_LANGS = ("en", "hi", "transliteration", "as", "bn", "gu", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te")

# Language fields that may be a single string (legacy) or a list of variants (new format).
# English (`en`) stays a plain string — it is the concept key / lookup key.
_MULTI_VALUE_LANG_FIELDS = (
    "hi",
    "transliteration",
    "bn",
    "te",
    "ta",
    "mr",
    "gu",
    "kn",
    "ml",
    "as_",
    "or_",
    "pa",
    "mai",
)


def _normalize_lang_values(value) -> list[str]:
    """Coerce a glossary language field to a list of non-empty strings.

    Supports both formats:
      - legacy:  "hi": "बीज"
      - multi:   "hi": ["बीज", "बीया", "बिया"]
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        terms: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    terms.append(stripped)
        return terms
    raise TypeError(
        f"Glossary language value must be str or list[str], got {type(value).__name__}"
    )


class TermPair(BaseModel):
    en: str = Field(description="English term (concept key)")
    hi: list[str] = Field(default_factory=list, description="Hindi term(s)")
    transliteration: list[str] = Field(
        default_factory=list,
        description="Transliteration(s) of Hindi term(s) to English script",
    )
    # Indic languages — empty list means not yet translated
    bn: list[str] = Field(default_factory=list, description="Bengali term(s)")
    te: list[str] = Field(default_factory=list, description="Telugu term(s)")
    ta: list[str] = Field(default_factory=list, description="Tamil term(s)")
    mr: list[str] = Field(default_factory=list, description="Marathi term(s)")
    gu: list[str] = Field(default_factory=list, description="Gujarati term(s)")
    kn: list[str] = Field(default_factory=list, description="Kannada term(s)")
    ml: list[str] = Field(default_factory=list, description="Malayalam term(s)")
    as_: list[str] = Field(default_factory=list, alias="as", description="Assamese term(s)")
    or_: list[str] = Field(default_factory=list, alias="or", description="Odia term(s)")
    pa: list[str] = Field(default_factory=list, description="Punjabi term(s)")
    mai: list[str] = Field(default_factory=list, description="Maithili term(s)")

    model_config = {"populate_by_name": True}

    @field_validator(*_MULTI_VALUE_LANG_FIELDS, mode="before")
    @classmethod
    def coerce_lang_values(cls, value) -> list[str]:
        return _normalize_lang_values(value)

    def get_terms(self, lang: str) -> list[str]:
        """All variants for a language code (empty list if missing)."""
        if lang in ("as", "or"):  # Python keywords, stored as `as_` / `or_`
            return list(getattr(self, f"{lang}_"))
        if lang == "en":
            return [self.en] if self.en else []
        value = getattr(self, lang, None)
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value] if value else []

    def get_term(self, lang: str) -> str:
        """Primary (first) term for a language — used for display / normalize.

        Callers that need every synonym should use get_terms() instead.
        """
        terms = self.get_terms(lang)
        return terms[0] if terms else ""

    def __str__(self):
        hi = self.get_term("hi")
        translit = self.get_term("transliteration")
        return f"{self.en} -> {hi} ({translit})"


# Convert raw dictionaries to TermPair objects
TERM_PAIRS = [TermPair(**pair) for pair in term_pairs]



# Every non-English surface form (all languages and transliterations) -> its English term.
REGIONAL_TO_EN = {
    variant.lower(): tp.en
    for tp in TERM_PAIRS
    for lang in SUPPORTED_LANGS
    if lang != "en"
    for variant in tp.get_terms(lang)
    if len(variant) >= 3
}
_MAX_TERM_WORDS = max((len(k.split()) for k in REGIONAL_TO_EN), default=1)


def to_english(text: str) -> str:
    """Replace glossary words written in a regional language or transliteration with the English term.

    Used by search_advisory on its query and crop, so a crop or pest the model passed as e.g. "bhindi"
    is searched as "Okra". Exact matches only (longest phrase first); English words are left alone.
    """
    words = (text or "").split()
    out, i = [], 0
    while i < len(words):
        for n in range(min(_MAX_TERM_WORDS, len(words) - i), 0, -1):
            phrase = " ".join(words[i:i + n]).lower().strip(".,?!")
            english = REGIONAL_TO_EN.get(phrase)
            if english and phrase not in EN_INDEX_KEYS:
                out.append(english)
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


### Utility functions for Correcting Document Search Results

# Build English index from glossary
EN_INDEX = {tp.en.lower(): tp for tp in TERM_PAIRS}
EN_TERMS = list(EN_INDEX.keys())
EN_INDEX_KEYS = set(EN_TERMS)

def build_glossary_pattern(terms):
    sorted_terms = sorted(terms, key=len, reverse=True)
    escaped = [re.escape(t) for t in sorted_terms]
    return r"\b(" + "|".join(escaped) + r")\b"

# Precompile regex pattern once
GLOSSARY_PATTERN = re.compile(build_glossary_pattern(EN_TERMS), flags=re.IGNORECASE)


def normalize_text_with_glossary(text: str, target_lang: str = "hi", threshold: int = 97) -> str:
    """Append the translated term in brackets next to English glossary terms.

    Args:
        text: Input text containing English agricultural terms.
        target_lang: Language code for the translation to append (default: "hi").
        threshold: Minimum fuzzy match score for glossary lookup (default: 97).
    """

    def replacer(match):
        word = match.group(0)
        lw = word.lower().strip()

        if lw in EN_INDEX:
            tp = EN_INDEX[lw]
        else:
            match_term, score, _ = process.extractOne(
                lw, EN_TERMS, score_cutoff=threshold
            ) or (None, 0, None)
            if not match_term:
                return word
            tp = EN_INDEX[match_term]

        translated = tp.get_term(target_lang)
        if not translated:
            return word

        after = match.end()
        if after < len(text) and text[after].isalnum():
            return f"{word} [{translated}] "
        else:
            return f"{word} [{translated}]"

    return GLOSSARY_PATTERN.sub(replacer, text)
