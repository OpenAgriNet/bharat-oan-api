import json
import re
from enum import Enum
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

# Load term pairs from JSON file with UTF-8 encoding
term_pairs = json.load(open('assets/glossary_terms.json', 'r', encoding='utf-8'))

SUPPORTED_LANGS = ("en", "hi", "transliteration", "as", "bn", "gu", "kn", "ml", "mr", "ta", "te")


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
    en: str = Field(description="English term")
    hi: str = Field(description="Hindi term")
    transliteration: str = Field(description="Transliteration of Hindi term to English")
    # Indic languages — empty string means not yet translated
    bn: str = Field(default="", description="Bengali term")
    te: str = Field(default="", description="Telugu term")
    ta: str = Field(default="", description="Tamil term")
    mr: str = Field(default="", description="Marathi term")
    gu: str = Field(default="", description="Gujarati term")
    kn: str = Field(default="", description="Kannada term")
    ml: str = Field(default="", description="Malayalam term")
    as_: str = Field(default="", alias="as", description="Assamese term")

    model_config = {"populate_by_name": True}

    def get_term(self, lang: str) -> str:
        """Get the term for a specific language code."""
        if lang == "as":
            return self.as_
        return getattr(self, lang, "")

    def __str__(self):
        return f"{self.en} -> {self.hi} ({self.transliteration})"


# Convert raw dictionaries to TermPair objects
TERM_PAIRS = [TermPair(**pair) for pair in term_pairs]


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
        language: Optional language to restrict search to (en/hi/transliteration/as/bn/gu/kn/ml/mr/ta/te)

    Returns:
        str: Formatted string with matching results and their scores
    """
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    matches = []
    term_lower = term.lower()

    # Determine which languages to search
    if language:
        search_langs = [language.value]
    else:
        search_langs = list(SUPPORTED_LANGS)

    for term_pair in TERM_PAIRS:
        max_score = 0.0

        for lang in search_langs:
            lang_term = term_pair.get_term(lang)
            if not lang_term:
                continue
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
