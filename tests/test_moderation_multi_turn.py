"""
Multi-turn moderation agent tests.

Runs conversations from tests/fixtures/moderation_multi_turn.json through the
production moderation agent and compares against expected categories.

History is formatted the same way as production (app/services/chat.py):
  message_pairs = "\\n\\n".join(format_message_pairs(history, 3))
  user_message  = f"{last_response}{deps.get_user_message()}"

Usage:
    .venv/bin/python3 -m tests.test_moderation_multi_turn
    .venv/bin/python3 -m tests.test_moderation_multi_turn --sample 5
    .venv/bin/python3 -m tests.test_moderation_multi_turn --concurrency 8
    .venv/bin/python3 -m tests.test_moderation_multi_turn --out results.json
"""

import asyncio
import argparse
import json
import random
import sys
import time
import logfire
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logfire.configure(scrubbing=False)

from agents.moderation import moderation_agent, QueryModerationResult
from langcodes import Language

QUESTIONS_PATH = ROOT / "tests" / "fixtures" / "moderation_multi_turn.json"

# Map fixture language keys to lang codes for langcodes library
LANG_CODE_MAP = {"en": "en", "hi": "hi", "hinglish": "hi"}


def format_history(history: list[dict], limit: int = 3) -> str:
    """Format conversation history the same way production does.

    Production flow (app/services/chat.py):
      message_pairs = "\\n\\n".join(format_message_pairs(history, 3))
      last_response = f"**Conversation**\\n\\n{message_pairs}\\n\\n---\\n\\n"

    format_message_pairs produces per pair:
      **User Message**:\\n{user}\\n\\n**Assistant Message**:\\n{assistant}
    """
    pairs = history[-limit:] if limit else history
    formatted = []
    for pair in pairs:
        formatted.append(
            f"**User Message**:\n{pair['user']}\n\n**Assistant Message**:\n{pair['assistant']}"
        )
    message_pairs = "\n\n".join(formatted)
    return f"**Conversation**\n\n{message_pairs}\n\n---\n\n"


def format_user_message(item: dict) -> str:
    """Build the full user message: history block + current question (like production)."""
    history = item.get("history", [])
    question = item["current_question"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)
    display_lang = Language.get(lang_code).display_name()

    # deps.get_user_message() output
    current = f'**User:** "{question}"\n**Selected Language:** {display_lang}'

    if history:
        conversation_block = format_history(history, limit=3)
        return f"{conversation_block}{current}"
    return current


# ANSI terminal colors
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
ERR  = "\033[93mERR \033[0m"


async def classify(item: dict) -> dict:
    """Run a single multi-turn item through the moderation agent."""
    question = item["current_question"]
    expected = item.get("expected_category")
    user_message = format_user_message(item)
    start = time.perf_counter()
    try:
        result = await moderation_agent.run(user_message)
        elapsed = time.perf_counter() - start
        output: QueryModerationResult = result.output
        match = output.category == expected if expected else None
        return {
            "question": question,
            "language": item.get("language", ""),
            "history_turns": len(item.get("history", [])),
            "expected": expected,
            "predicted": output.category,
            "action": output.action,
            "match": match,
            "elapsed_s": round(elapsed, 2),
            "error": None,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "question": question,
            "language": item.get("language", ""),
            "history_turns": len(item.get("history", [])),
            "expected": expected,
            "predicted": None,
            "action": None,
            "match": False,
            "elapsed_s": round(elapsed, 2),
            "error": f"{type(e).__name__}: {e}",
        }


async def run_all(items: list[dict], concurrency: int = 4) -> list[dict]:
    """Classify items with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)
    results: list[dict | None] = [None] * len(items)
    done_count = 0
    total = len(items)

    async def _worker(idx: int, item: dict):
        nonlocal done_count
        async with sem:
            r = await classify(item)
            results[idx] = r
            done_count += 1

            if r["error"]:
                tag = ERR
            elif r["match"]:
                tag = PASS
            else:
                tag = FAIL

            pred = r["predicted"] or "ERROR"
            print(f"[{done_count:3d}/{total}] {tag} {pred:40s} | {r['question'][:65]}")

    await asyncio.gather(*[_worker(i, item) for i, item in enumerate(items)])
    return results


def print_summary(results: list[dict]) -> None:
    """Print accuracy summary and mismatches."""
    errors = [r for r in results if r["error"]]
    scored = [r for r in results if r["expected"] and not r["error"]]
    correct = sum(1 for r in scored if r["match"])
    total_time = sum(r["elapsed_s"] for r in results)

    print("\n" + "=" * 70)
    print("ACCURACY")
    print("=" * 70)
    if scored:
        pct = correct / len(scored) * 100
        print(f"  {correct}/{len(scored)} correct ({pct:.1f}%)")
    if errors:
        print(f"  {len(errors)} errors")

    # Per-category breakdown
    by_cat = {}
    for r in scored:
        cat = r["expected"]
        by_cat.setdefault(cat, {"total": 0, "correct": 0})
        by_cat[cat]["total"] += 1
        if r["match"]:
            by_cat[cat]["correct"] += 1

    print(f"\n  {'Category':<40} {'Acc':>7}")
    print("  " + "-" * 48)
    for cat in sorted(by_cat):
        c = by_cat[cat]["correct"]
        t = by_cat[cat]["total"]
        print(f"  {cat:<40} {c}/{t:>3} ({c/t*100:5.1f}%)")

    # Show mismatches
    mismatches = [r for r in scored if not r["match"]]
    if mismatches:
        print(f"\n{'=' * 70}")
        print(f"MISMATCHES ({len(mismatches)})")
        print("=" * 70)
        for r in mismatches:
            print(f"  expected={r['expected']}")
            print(f"  predicted={r['predicted']}")
            print(f"  Q: {r['question'][:80]}")
            print()

    print(f"\nTime: {total_time:.1f}s total | {total_time / len(results):.2f}s avg/question")


def main():
    parser = argparse.ArgumentParser(description="Multi-turn moderation tests")
    parser.add_argument("--out", type=str, help="Save full results to JSON file")
    parser.add_argument("--sample", type=int, default=0, help="Run on N random items (0 = all)")
    parser.add_argument("--concurrency", type=int, default=4, help="Max parallel requests (default: 4)")
    args = parser.parse_args()

    if not QUESTIONS_PATH.exists():
        print(f"ERROR: Questions file not found at {QUESTIONS_PATH}")
        sys.exit(1)

    items = json.loads(QUESTIONS_PATH.read_text())

    if args.sample and args.sample < len(items):
        items = random.SystemRandom().sample(items, args.sample)
        print(f"Sampled {args.sample} conversations from {QUESTIONS_PATH.name}\n")
    else:
        print(f"Loaded {len(items)} conversations from {QUESTIONS_PATH.name}\n")

    results = asyncio.run(run_all(items, concurrency=args.concurrency))
    print_summary(results)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
