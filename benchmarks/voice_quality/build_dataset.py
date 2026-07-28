"""Build the 10-questions-per-language benchmark set from the Gemma comparison workbook.

Questions are keyed by `session_id`, which is aligned across every language sheet
(session 1 is the same question translated into each of the 10 languages), so a
sampled id can be looked up in all languages at once.

Usage:
    .venv/bin/python3 -m benchmarks.voice_quality.build_dataset
    .venv/bin/python3 -m benchmarks.voice_quality.build_dataset --seed 42 --per-lang 10
"""

import argparse
import json
import random
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "data"
WORKBOOK = Path.home() / "Downloads" / "gemmacomparison.xlsx"

# Sheet file-column prefix -> BCP-47 code the agrinet agent expects.
LANGUAGES = {
    "assamese": "as",
    "bengali": "bn",
    "english": "en",
    "gujarati": "gu",
    "hindi": "hi",
    "kannada": "kn",
    "malayalam": "ml",
    "marathi": "mr",
    "tamil": "ta",
    "telugu": "te",
}

# Sessions 160-185 are multi-turn PM-KISAN status/grievance transcripts that need a
# real registration number to replay. Out of scope for a single-turn voice benchmark.
LAST_SINGLE_TURN_SESSION = 159

# search_schemes/search_video are Qdrant-backed and QDRANT_URL is unset locally, so
# any question that would route there is excluded rather than scored as a failure.
# Everything else lands on get_scheme_info (the 16 legacy Beckn schemes).
QDRANT_ROUTED_SESSIONS = {
    34,   # poultry farming scheme - no legacy match
    42,   # "what schemes are available" - generic, falls through to search_schemes
    53,   # farm pond scheme - no legacy match
    64,   # farm pond scheme
    73,   # orchard plantation scheme -> MIDH (Qdrant)
    78,   # "special schemes for small farmers" - generic
    84,   # farm pond scheme
    86,   # orchard plantation scheme -> MIDH (Qdrant)
    111,  # Nanaji Deshmukh Krishi Sanjivani Yojana - state scheme, not indexed
    112,  # "government scheme for sunflower" - generic
    145,  # polyhouse subsidy -> MIDH (Qdrant)
}

# Checked in order; first match wins.
CATEGORY_PATTERNS = [
    ("livestock", r"\b(cow|buffalo|poultry|calves|calf|milch|newcastle|udder|deworm|milk)\b"),
    ("mandi", r"\b(mandi|market price|market prices|mandi prices|rates of|price of|prices for|msp)\b"),
    ("weather", r"\b(weather|rain|rainfall|temperature|wind|cold|hailstorm|cloudy|dry|forecast)\b"),
    ("scheme", r"\b(scheme|pm-kisan|pmfby|insurance|subsidy|eligibility|documents are required|yojana)\b"),
    ("pest_disease", r"\b(pesticide|whitefly|borer|thrips|jassids|armyworm|bollworm|mildew|blight|"
                     r"root rot|leaf curl|symptoms|natural enemies|biological control|yellow spots|weeds)\b"),
]


def categorize(question_en: str) -> str:
    q = question_en.lower()
    for name, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, q):
            return name
    return "crop_advisory"


def load_questions() -> dict[str, dict[int, str]]:
    """Return {lang_code: {session_id: question_text}} from the gemma results sheet."""
    workbook = openpyxl.load_workbook(WORKBOOK, data_only=True)
    sheet = workbook["gemma results"]
    rows = list(sheet.iter_rows(values_only=True))
    header = list(rows[0])
    file_i, session_i, question_i = (header.index(c) for c in ("file", "session_id", "question"))

    by_lang: dict[str, dict[int, str]] = {code: {} for code in LANGUAGES.values()}
    for row in rows[1:]:
        raw_file = row[file_i]
        if not raw_file:
            continue
        prefix = str(raw_file).split("_")[0]
        code = LANGUAGES.get(prefix)
        if code is None or row[session_i] is None or not row[question_i]:
            continue
        by_lang[code][int(row[session_i])] = str(row[question_i]).strip()
    return by_lang


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--per-lang", type=int, default=10)
    args = parser.parse_args()

    by_lang = load_questions()
    english = by_lang["en"]

    eligible = sorted(
        sid for sid in english
        if sid <= LAST_SINGLE_TURN_SESSION and sid not in QDRANT_ROUTED_SESSIONS
    )
    # Only sample ids that exist in every language, so a sampled id is always resolvable.
    eligible = [sid for sid in eligible if all(sid in by_lang[c] for c in LANGUAGES.values())]

    categories = {sid: categorize(english[sid]) for sid in eligible}

    rng = random.Random(args.seed)
    items = []
    for code in LANGUAGES.values():
        for sid in sorted(rng.sample(eligible, args.per_lang)):
            items.append({
                "session_id": sid,
                "language": code,
                "category": categories[sid],
                "question": by_lang[code][sid],
                "question_en": english[sid],
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset = {
        "seed": args.seed,
        "per_language": args.per_lang,
        "eligible_pool": eligible,
        "excluded_qdrant_routed": sorted(QDRANT_ROUTED_SESSIONS),
        "excluded_multi_turn": f">{LAST_SINGLE_TURN_SESSION}",
        "items": items,
    }
    out_path = OUT_DIR / "dataset.json"
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pool={len(eligible)} eligible sessions, {len(items)} items -> {out_path}")
    by_cat: dict[str, int] = {}
    for item in items:
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
    print("category mix:", dict(sorted(by_cat.items(), key=lambda kv: -kv[1])))
    for code in LANGUAGES.values():
        sids = [i["session_id"] for i in items if i["language"] == code]
        print(f"  {code}: {sids}")


if __name__ == "__main__":
    main()
