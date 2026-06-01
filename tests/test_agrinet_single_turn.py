"""
Single-turn agrinet (full pipeline) performance tests.

Runs questions from tests/fixtures/agrinet_single_turn.json through the
full pipeline: moderation → agrinet agent (with tools) and measures
latency, errors, and tool usage per category.

Usage:
    .venv/bin/python3 -m tests.test_agrinet_single_turn
    .venv/bin/python3 -m tests.test_agrinet_single_turn --sample 10
    .venv/bin/python3 -m tests.test_agrinet_single_turn --concurrency 4
    .venv/bin/python3 -m tests.test_agrinet_single_turn --out results.json
    .venv/bin/python3 -m tests.test_agrinet_single_turn --category weather
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

from agents.moderation import moderation_agent
from agents.agrinet import agrinet_agent
from agents.deps import FarmerContext
from pydantic_ai import Agent
from langcodes import Language

QUESTIONS_PATH = ROOT / "tests" / "fixtures" / "agrinet_single_turn.json"

LANG_CODE_MAP = {"en": "en", "hi": "hi"}

# ANSI colors
OK   = "\033[92m OK \033[0m"
ERR  = "\033[91mERR \033[0m"
WARN = "\033[93mWRN\033[0m"


def format_moderation_message(item: dict) -> str:
    """Format user message for moderation agent (same as production)."""
    question = item["question"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)
    display_lang = Language.get(lang_code).display_name()
    return f'**User:** "{question}"\n**Selected Language:** {display_lang}'


async def run_single(item: dict) -> dict:
    """Run a single question through the full pipeline."""
    question = item["question"]
    category = item["category"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)

    start = time.perf_counter()
    moderation_elapsed = 0.0
    agent_elapsed = 0.0
    tools_called = []
    mod_category = None

    try:
        # Step 1: Moderation (same as production chat.py)
        mod_start = time.perf_counter()
        mod_message = format_moderation_message(item)
        mod_result = await moderation_agent.run(mod_message)
        moderation_elapsed = time.perf_counter() - mod_start
        mod_output = mod_result.output
        mod_category = mod_output.category

        # Step 2: Agrinet agent via .iter() — mirrors production chat.py flow
        agent_start = time.perf_counter()
        deps = FarmerContext(
            query=question,
            lang_code=lang_code,
            session_id=f"test_{category}_{id(item)}",
        )
        deps.update_moderation_str(str(mod_output))
        user_message = deps.get_user_message()

        response_text = ""
        async with agrinet_agent.iter(
            user_prompt=user_message,
            message_history=[],
            deps=deps,
        ) as agent_run:
            async for node in agent_run:
                if Agent.is_call_tools_node(node):
                    continue
                elif Agent.is_end_node(node):
                    break

        agent_elapsed = time.perf_counter() - agent_start

        # Extract tool calls and final output from the run result
        if agent_run.result:
            response_text = agent_run.result.output or ""
            for msg in agent_run.result.new_messages():
                for part in msg.parts:
                    if getattr(part, "part_kind", "") == "tool-call":
                        tools_called.append(part.tool_name)

        total_elapsed = time.perf_counter() - start

        return {
            "question": question,
            "category": category,
            "language": lang,
            "moderation": mod_category,
            "tools_called": tools_called,
            "response_length": len(response_text),
            "response_preview": response_text,
            "moderation_s": round(moderation_elapsed, 2),
            "agent_s": round(agent_elapsed, 2),
            "total_s": round(total_elapsed, 2),
            "error": None,
        }

    except Exception as e:
        total_elapsed = time.perf_counter() - start
        return {
            "question": question,
            "category": category,
            "language": lang,
            "moderation": mod_category,
            "tools_called": tools_called,
            "response_length": 0,
            "response_preview": "",
            "moderation_s": round(moderation_elapsed, 2),
            "agent_s": round(agent_elapsed, 2),
            "total_s": round(total_elapsed, 2),
            "error": f"{type(e).__name__}: {e}",
        }


async def run_all(items: list[dict], concurrency: int = 4) -> list[dict]:
    """Run all questions with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)
    results: list[dict | None] = [None] * len(items)
    done_count = 0
    total = len(items)

    async def _worker(idx: int, item: dict):
        nonlocal done_count
        async with sem:
            r = await run_single(item)
            results[idx] = r
            done_count += 1

            tag = ERR if r["error"] else OK
            tools_str = ", ".join(r["tools_called"][:3]) if r["tools_called"] else "none"
            print(
                f"[{done_count:3d}/{total}] {tag} "
                f"{r['category']:20s} | {r['total_s']:5.1f}s | "
                f"tools: {tools_str:40s} | {r['question'][:50]}"
            )

    await asyncio.gather(*[_worker(i, item) for i, item in enumerate(items)])
    return results


def print_summary(results: list[dict]) -> None:
    """Print performance summary by category."""
    errors = [r for r in results if r["error"]]
    ok = [r for r in results if not r["error"]]

    total_time = sum(r["total_s"] for r in results)
    total_mod_time = sum(r["moderation_s"] for r in results)
    total_agent_time = sum(r["agent_s"] for r in results)

    print("\n" + "=" * 80)
    print("PERFORMANCE SUMMARY")
    print("=" * 80)

    print(f"\n  Total questions:  {len(results)}")
    print(f"  Successful:       {len(ok)}")
    print(f"  Errors:           {len(errors)}")

    print(f"\n  Total time:       {total_time:.1f}s")
    print(f"  Avg total/q:      {total_time / len(results):.2f}s")
    print(f"  Avg moderation/q: {total_mod_time / len(results):.2f}s")
    print(f"  Avg agent/q:      {total_agent_time / len(results):.2f}s")

    # Per-category breakdown
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    print(f"\n  {'Category':<20} {'N':>3} {'Err':>4} {'Avg Tot':>8} {'Avg Mod':>8} {'Avg Agt':>8} {'Avg Resp':>8}")
    print("  " + "-" * 72)
    for cat in sorted(by_cat):
        items = by_cat[cat]
        n = len(items)
        errs = sum(1 for r in items if r["error"])
        avg_total = sum(r["total_s"] for r in items) / n
        avg_mod = sum(r["moderation_s"] for r in items) / n
        avg_agent = sum(r["agent_s"] for r in items) / n
        avg_resp = sum(r["response_length"] for r in items) / n
        print(
            f"  {cat:<20} {n:3d} {errs:4d} "
            f"{avg_total:7.2f}s {avg_mod:7.2f}s {avg_agent:7.2f}s "
            f"{avg_resp:7.0f}ch"
        )

    # Per-language breakdown
    by_lang: dict[str, list[dict]] = {}
    for r in results:
        by_lang.setdefault(r["language"], []).append(r)

    print(f"\n  {'Language':<20} {'N':>3} {'Err':>4} {'Avg Tot':>8} {'Avg Agt':>8}")
    print("  " + "-" * 45)
    for lang in sorted(by_lang):
        items = by_lang[lang]
        n = len(items)
        errs = sum(1 for r in items if r["error"])
        avg_total = sum(r["total_s"] for r in items) / n
        avg_agent = sum(r["agent_s"] for r in items) / n
        print(f"  {lang:<20} {n:3d} {errs:4d} {avg_total:7.2f}s {avg_agent:7.2f}s")

    # Tool usage stats
    from collections import Counter
    tool_counter = Counter()
    for r in ok:
        for t in r["tools_called"]:
            tool_counter[t] += 1

    if tool_counter:
        print(f"\n  Tool Usage (across {len(ok)} successful runs):")
        print("  " + "-" * 40)
        for tool, count in tool_counter.most_common():
            print(f"  {tool:<35} {count:4d}")

    # Moderation results
    mod_counter = Counter(r["moderation"] for r in results)
    print(f"\n  Moderation Results:")
    print("  " + "-" * 40)
    for mod, count in mod_counter.most_common():
        print(f"  {mod or 'ERROR':<35} {count:4d}")

    # Errors detail
    if errors:
        print(f"\n{'=' * 80}")
        print(f"ERRORS ({len(errors)})")
        print("=" * 80)
        for r in errors:
            print(f"  [{r['category']}] {r['question'][:60]}")
            print(f"    {r['error']}")
            print()

    print(f"\nTime: {total_time:.1f}s total | {total_time / len(results):.2f}s avg/question")


def main():
    parser = argparse.ArgumentParser(description="Single-turn agrinet pipeline performance tests")
    parser.add_argument("--out", type=str, help="Save full results to JSON file")
    parser.add_argument("--sample", type=int, default=0, help="Run on N random questions (0 = all)")
    parser.add_argument("--concurrency", type=int, default=4, help="Max parallel requests (default: 4)")
    parser.add_argument("--category", type=str, default="", help="Filter to a specific category")
    args = parser.parse_args()

    if not QUESTIONS_PATH.exists():
        print(f"ERROR: Questions file not found at {QUESTIONS_PATH}")
        sys.exit(1)

    items = json.loads(QUESTIONS_PATH.read_text())

    if args.category:
        items = [i for i in items if i["category"] == args.category]
        if not items:
            print(f"ERROR: No questions found for category '{args.category}'")
            sys.exit(1)
        print(f"Filtered to {len(items)} questions in category '{args.category}'")

    if args.sample and args.sample < len(items):
        items = random.SystemRandom().sample(items, args.sample)
        print(f"Sampled {args.sample} questions from {QUESTIONS_PATH.name}\n")
    else:
        print(f"Loaded {len(items)} questions from {QUESTIONS_PATH.name}\n")

    results = asyncio.run(run_all(items, concurrency=args.concurrency))
    print_summary(results)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
