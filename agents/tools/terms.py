import json
import re
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz, process
from langfuse import observe

# Load term pairs from JSON file with UTF-8 encoding
term_pairs = json.load(open('assets/glossary_terms.json', 'r', encoding='utf-8'))

SUPPORTED_LANGS = ("en", "hi", "transliteration", "as", "bn", "gu", "kn", "ml", "mr", "ta", "te")

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


class Language(str, Enum):
    ENGLISH = "en"
    HINDI = "hi"
    TRANSLITERATION = "transliteration"
    ASSAMESE = "as"
    BENGALI = "bn"
    GUJARATI = "gu"
    KANNADA = "kn"
    MALAYALAM = "ml"
    MARATHI = "mr"
    TAMIL = "ta"
    TELUGU = "te"


class TermPair(BaseModel):
    en: str = Field(description="English term (concept key)")
    hi: list[str] = Field(default=[], description="Hindi term(s)")
    transliteration: list[str] = Field(
        default=[],
        description="Transliteration(s) of Hindi term(s) to English script",
    )
    # Indic languages — empty list means not yet translated
    bn: list[str] = Field(default=[], description="Bengali term(s)")
    te: list[str] = Field(default=[], description="Telugu term(s)")
    ta: list[str] = Field(default=[], description="Tamil term(s)")
    mr: list[str] = Field(default=[], description="Marathi term(s)")
    gu: list[str] = Field(default=[], description="Gujarati term(s)")
    kn: list[str] = Field(default=[], description="Kannada term(s)")
    ml: list[str] = Field(default=[], description="Malayalam term(s)")
    as_: list[str] = Field(default=[], alias="as", description="Assamese term(s)")

    model_config = {"populate_by_name": True}

    @field_validator(*_MULTI_VALUE_LANG_FIELDS, mode="before")
    @classmethod
    def coerce_lang_values(cls, value) -> list[str]:
        return _normalize_lang_values(value)

    def get_terms(self, lang: str) -> list[str]:
        """All variants for a language code (empty list if missing)."""
        if lang == "as":
            return list(self.as_)
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



@observe(name="tool:search_terms", as_type="tool")
async def search_terms(
    term: str,
    max_results: int = 5,
    threshold: float = 0.7,
    language: Language | None = None,
) -> str:
    """Search for terms using fuzzy partial string matching across all fields.

    Args:
        term: The term to search for
        max_results: Maximum number of results to return
        threshold: Minimum similarity score (0-1) to consider a match (default is 0.7)
        language: Optional language to restrict search to (en/hi/transliteration/as/bn/gu/kn/ml/mr/ta/te).
            IMPORTANT: pick this based on the actual script/language the query
            `term` is written in — not on what field you expect the answer to
            come from. Latin/Roman script is not the same as English: a
            romanized Indic-language word (e.g. "murjhaane ka rog", "beej")
            belongs to "transliteration", not "en" — "en" is only for terms
            that are actually English words (e.g. "Wilt", "Seed"). Likewise,
            a Devanagari term is "hi", a Bengali-script term is "bn", and so
            on for every other supported language — always match the code to
            the query's script, never guess. Mismatching them causes fuzzy
            matching to fail even when the right entry exists. If you are not
            sure which language/script the query is in, omit `language`
            entirely so every field is searched.

    Returns:
        str: Formatted string with matching results and their scores
    """
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    matches = []
    term_lower = term.lower()

    # Determine which languages to search
    # Accept either a Language enum member or a plain string (e.g. "transliteration")
    # so callers that don't go through pydantic validation still work.
    if language:
        search_langs = [language.value if isinstance(language, Language) else language]
    else:
        search_langs = list(SUPPORTED_LANGS)

    for term_pair in TERM_PAIRS:
        max_score = 0.0

        # Score every surface form for each language and keep the best.
        # That way dialect/synonym variants (not only the primary string) match.
        for lang in search_langs:
            for lang_term in term_pair.get_terms(lang):
                score = fuzz.ratio(term_lower, lang_term.lower()) / 100.0
                max_score = max(max_score, score)

        if max_score >= threshold:
            matches.append((term_pair, max_score))

    matches.sort(key=lambda x: x[1], reverse=True)

    if matches:
        matches = matches[:max_results]
        return f"Matching Terms for `{term}`\n\n" + "\n".join(
            f"{m[0]} [{m[1]:.0%}]" for m in matches
        )
    else:
        return f"No matching terms found for `{term}`"


### Utility functions for Correcting Document Search Results

# Build English index from glossary
EN_INDEX = {tp.en.lower(): tp for tp in TERM_PAIRS}
EN_TERMS = list(EN_INDEX.keys())

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
