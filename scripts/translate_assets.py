"""
Translate commodity_codes.json and glossary_terms.json to 8 new Indic languages
using Claude API.

Usage:
    # Migrate commodity_codes.json flat terms -> per-language dict (no API needed)
    python scripts/translate_assets.py migrate-commodity

    # Translate glossary terms to all new languages
    python scripts/translate_assets.py translate-glossary

    # Translate commodity codes to all new languages
    python scripts/translate_assets.py translate-commodity

Requires ANTHROPIC_API_KEY environment variable.
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
CHECKPOINT_DIR = Path(__file__).resolve().parent / ".checkpoints"

TARGET_LANGS = ["as", "bn", "gu", "kn", "ml", "mr", "ta", "te"]
LANG_NAMES = {
    "as": "Assamese",
    "bn": "Bengali",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ta": "Tamil",
    "te": "Telugu",
}

BATCH_SIZE = 50  # terms per API call
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def is_devanagari(text: str) -> bool:
    """Check if text contains Devanagari script characters."""
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            try:
                name = unicodedata.name(ch, "")
                if "DEVANAGARI" in name:
                    return True
            except ValueError:
                pass
    return False


def is_latin(text: str) -> bool:
    """Check if text is purely Latin script (ASCII letters + common punctuation)."""
    return all(
        ch.isascii() or not unicodedata.category(ch).startswith("L")
        for ch in text
    )


def migrate_commodity_codes():
    """
    Migrate commodity_codes.json from flat terms list to per-language dict.
    Splits existing terms into 'en' (Latin) and 'hi' (Devanagari + transliterations).
    """
    path = ASSETS_DIR / "commodity_codes.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    migrated = []
    for entry in data:
        old_terms = entry.get("terms", [])

        # First term is usually the English name
        en_terms = []
        hi_terms = []

        for term in old_terms:
            if is_devanagari(term):
                hi_terms.append(term)
            elif is_latin(term):
                # Check if it looks like a Hindi transliteration
                # (lowercase, no spaces typically, and there's already a Devanagari term)
                lower = term.lower()
                name_lower = entry["name"].lower().replace("(", "").replace(")", "")
                # If the term matches part of the English name, it's English
                if lower in name_lower or name_lower.startswith(lower.split()[0]):
                    en_terms.append(term)
                elif any(is_devanagari(t) for t in old_terms):
                    # Has Devanagari siblings → likely transliteration → put in hi
                    hi_terms.append(term)
                else:
                    en_terms.append(term)
            else:
                # Non-Latin, non-Devanagari → keep in hi bucket for now
                hi_terms.append(term)

        terms_dict = {"en": en_terms, "hi": hi_terms}
        for lang in TARGET_LANGS:
            terms_dict[lang] = []

        migrated.append({
            "code": entry["code"],
            "name": entry["name"],
            "terms": terms_dict,
        })

    path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Migrated {len(migrated)} commodity entries to per-language dict structure.")

    # Show a few examples for verification
    for e in migrated[:5]:
        print(f"  {e['name']}: en={e['terms']['en']}, hi={e['terms']['hi']}")


def add_glossary_placeholders():
    """Add empty string placeholders for new languages in glossary_terms.json."""
    path = ASSETS_DIR / "glossary_terms.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    for entry in data:
        for lang in TARGET_LANGS:
            if lang not in entry:
                entry[lang] = ""

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Added placeholder keys for {TARGET_LANGS} to {len(data)} glossary entries.")


def get_client():
    """Get Anthropic client, loading API key from .env if needed."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
    try:
        import anthropic
    except ImportError:
        print("Error: anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)
    return anthropic.Anthropic()


def load_checkpoint(name: str) -> int:
    """Load checkpoint index for resuming translation."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp_file = CHECKPOINT_DIR / f"{name}.json"
    if cp_file.exists():
        cp = json.loads(cp_file.read_text())
        return cp.get("last_index", 0)
    return 0


def save_checkpoint(name: str, last_index: int):
    """Save checkpoint index."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp_file = CHECKPOINT_DIR / f"{name}.json"
    cp_file.write_text(json.dumps({"last_index": last_index}))


def translate_batch(client, terms: list[str], lang_code: str) -> list[dict]:
    """
    Translate a batch of English terms to a target language using Claude.
    Returns list of {"term": str, "transliteration": str} dicts.
    """
    lang_name = LANG_NAMES[lang_code]
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(terms))

    prompt = f"""Translate these agricultural/farming terms from English to {lang_name}.

Return a JSON array where each element has exactly two keys:
- "term": the translation in {lang_name} script
- "transliteration": romanized transliteration of the {lang_name} term

Example for Bengali: [{{"term": "গম", "transliteration": "gom"}}, ...]

Keep the same order as input. If a term has no good translation, transliterate the English.

Terms:
{numbered}

Return ONLY the JSON array, no other text."""

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Strip markdown fencing if present
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            raw_result = json.loads(text)
            # Normalize keys — model sometimes uses language name as key instead of "term"
            result = []
            for item in raw_result:
                if "term" in item:
                    result.append({"term": item["term"], "transliteration": item.get("transliteration", "")})
                else:
                    # Find the non-transliteration key
                    keys = [k for k in item if k != "transliteration"]
                    term_val = item[keys[0]] if keys else ""
                    result.append({"term": term_val, "transliteration": item.get("transliteration", "")})
            if len(result) != len(terms):
                print(f"  Warning: expected {len(terms)} results, got {len(result)}")
                while len(result) < len(terms):
                    result.append({"term": terms[len(result)], "transliteration": terms[len(result)].lower()})
                result = result[:len(terms)]
            return result
        except Exception as e:
            print(f"  Attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    # Final fallback: return English terms as-is
    return [{"term": t, "transliteration": t.lower()} for t in terms]


def translate_glossary_lang(lang: str):
    """Translate glossary_terms.json for a single language. Safe for parallel use with file locking."""
    import fcntl

    client = get_client()
    path = ASSETS_DIR / "glossary_terms.json"
    lock_path = ASSETS_DIR / "glossary_terms.json.lock"

    cp_name = f"glossary_{lang}"
    start_idx = load_checkpoint(cp_name)

    # Read current data
    data = json.loads(path.read_text(encoding="utf-8"))

    needs_translation = [
        (i, entry) for i, entry in enumerate(data)
        if i >= start_idx and not entry.get(lang)
    ]
    if not needs_translation:
        print(f"  {LANG_NAMES[lang]} ({lang}): already complete")
        return

    print(f"  {LANG_NAMES[lang]} ({lang}): {len(needs_translation)} entries to translate, starting from index {start_idx}")

    for batch_start in range(0, len(needs_translation), BATCH_SIZE):
        batch = needs_translation[batch_start:batch_start + BATCH_SIZE]
        en_terms = [entry["en"] for _, entry in batch]
        results = translate_batch(client, en_terms, lang)

        # Lock, re-read, update, write, unlock
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            fresh_data = json.loads(path.read_text(encoding="utf-8"))
            for (idx, _), result in zip(batch, results):
                fresh_data[idx][lang] = result["term"]
            path.write_text(
                json.dumps(fresh_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            fcntl.flock(lock_file, fcntl.LOCK_UN)

        last_idx = batch[-1][0]
        save_checkpoint(cp_name, last_idx + 1)

        done = batch_start + len(batch)
        print(f"    Batch {done}/{len(needs_translation)} done")

    print(f"  {LANG_NAMES[lang]}: complete")


def translate_glossary(lang: str | None = None):
    """Translate glossary_terms.json. If lang specified, only that language."""
    langs = [lang] if lang else TARGET_LANGS
    for l in langs:
        translate_glossary_lang(l)


def translate_commodity():
    """Translate commodity_codes.json to all target languages."""
    client = get_client()
    path = ASSETS_DIR / "commodity_codes.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    # Verify structure has been migrated
    if data and isinstance(data[0].get("terms"), list):
        print("Error: commodity_codes.json has not been migrated yet.")
        print("Run: python scripts/translate_assets.py migrate-commodity")
        sys.exit(1)

    for lang in TARGET_LANGS:
        cp_name = f"commodity_{lang}"
        start_idx = load_checkpoint(cp_name)

        needs_translation = [
            (i, entry) for i, entry in enumerate(data)
            if i >= start_idx and not entry["terms"].get(lang)
        ]
        if not needs_translation:
            print(f"  {LANG_NAMES[lang]} ({lang}): already complete")
            continue

        print(f"  {LANG_NAMES[lang]} ({lang}): {len(needs_translation)} entries to translate")

        for batch_start in range(0, len(needs_translation), BATCH_SIZE):
            batch = needs_translation[batch_start:batch_start + BATCH_SIZE]
            en_terms = [entry["name"] for _, entry in batch]
            results = translate_batch(client, en_terms, lang)

            for (idx, entry), result in zip(batch, results):
                entry["terms"][lang] = [result["term"], result["transliteration"]]

            last_idx = batch[-1][0]
            save_checkpoint(cp_name, last_idx + 1)

            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            done = batch_start + len(batch)
            print(f"    Batch {done}/{len(needs_translation)} done")

        print(f"  {LANG_NAMES[lang]}: complete")


def main():
    parser = argparse.ArgumentParser(description="Translate agricultural assets to Indic languages")
    parser.add_argument(
        "command",
        choices=["migrate-commodity", "add-glossary-placeholders", "translate-glossary", "translate-commodity"],
        help="Command to run",
    )
    parser.add_argument(
        "--lang",
        choices=TARGET_LANGS,
        help="Translate only this language (for parallel runs)",
    )
    args = parser.parse_args()

    if args.command == "migrate-commodity":
        migrate_commodity_codes()
    elif args.command == "add-glossary-placeholders":
        add_glossary_placeholders()
    elif args.command == "translate-glossary":
        print(f"Translating glossary terms ({args.lang or 'all'})...")
        translate_glossary(args.lang)
        print("Done!")
    elif args.command == "translate-commodity":
        print("Translating commodity codes...")
        translate_commodity()
        print("Done!")


if __name__ == "__main__":
    main()
