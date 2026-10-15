"""
Run a sample of questions and check responses for JSON/code leakage.

Detects:
  - Raw JSON objects: { "key": ... }
  - Tool parameter keys appearing as text: "term", "threshold", "query", etc.
  - Thinking traces: <think> / </think>
  - JSON code blocks: ```json
  - Raw function call syntax: search_terms(, forward_geocode(, etc.

Usage:
    .venv/bin/python3 -m tests.check_json_in_responses
    .venv/bin/python3 -m tests.check_json_in_responses --sample 20
"""

import asyncio
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import logfire
logfire.configure(scrubbing=False)

from agents.moderation import moderation_agent
from agents.agrinet import agrinet_agent
from agents.deps import FarmerContext
from pydantic_ai import Agent
from langcodes import Language

QUESTIONS_PATH = ROOT / "tests" / "fixtures" / "agrinet_single_turn.json"
LANG_CODE_MAP = {"en": "en", "hi": "hi"}

# Patterns that indicate JSON/code leakage in responses
JSON_PATTERNS = [
    (re.compile(r'\{[^}]*"[^"]+"\s*:'), "JSON object"),
    (re.compile(r'"(term|threshold|query|scheme_name|phone_number|latitude|longitude|max_results|top_k|place_name|commodity_code|days_back|inquiry_type|season|cycle|reg_no|otp|identity_no|grievance_type|grievance_description)"\s*:'), "tool param key"),
    (re.compile(r'<think>'), "thinking trace start"),
    (re.compile(r'</think>'), "thinking trace end"),
    (re.compile(r'```json'), "json code block"),
    (re.compile(r'```python'), "python code block"),
    (re.compile(r'(search_terms|search_documents|search_pests_diseases|search_videos|forward_geocode|reverse_geocode|weather_forecast|get_mandi_prices|search_commodity|get_scheme_info|check_pmfby_status|check_shc_status|initiate_pm_kisan_status_check|check_pm_kisan_status_with_otp|pmkisan_submit_grievance|pmkisan_grievance_status)\s*\('), "raw function call"),
]

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def check_response(text: str) -> list[tuple[str, str]]:
    """Check a response for JSON/code patterns. Returns list of (pattern_name, matched_text)."""
    findings = []
    for pattern, name in JSON_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            for m in matches:
                findings.append((name, m if isinstance(m, str) else str(m)))
    return findings


def format_moderation_message(item: dict) -> str:
    question = item["question"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)
    display_lang = Language.get(lang_code).display_name()
    return f'**User:** "{question}"\n**Selected Language:** {display_lang}'


async def run_single(item: dict) -> dict:
    question = item["question"]
    category = item["category"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)

    start = time.perf_counter()
    tools_called = []

    try:
        mod_message = format_moderation_message(item)
        mod_result = await moderation_agent.run(mod_message)
        mod_output = mod_result.output

        deps = FarmerContext(
            query=question,
            lang_code=lang_code,
            session_id=f"test_json_{category}_{id(item)}",
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

        elapsed = time.perf_counter() - start

        if agent_run.result:
            response_text = agent_run.result.output or ""
            for msg in agent_run.result.new_messages():
                for part in msg.parts:
                    if getattr(part, "part_kind", "") == "tool-call":
                        tools_called.append(part.tool_name)

        findings = check_response(response_text)

        return {
            "question": question,
            "category": category,
            "language": lang,
            "tools": tools_called,
            "response": response_text,
            "time_s": round(elapsed, 1),
            "json_findings": findings,
            "error": None,
        }

    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "question": question,
            "category": category,
            "language": lang,
            "tools": tools_called,
            "response": "",
            "time_s": round(elapsed, 1),
            "json_findings": [],
            "error": f"{type(e).__name__}: {e}",
        }


async def run_all(items: list[dict], concurrency: int = 2) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    results = [None] * len(items)
    done_count = 0
    total = len(items)

    async def _worker(idx, item):
        nonlocal done_count
        async with sem:
            r = await run_single(item)
            results[idx] = r
            done_count += 1

            status = f"{RED}ERR{RESET}" if r["error"] else (
                f"{YELLOW}JSON{RESET}" if r["json_findings"] else f"{GREEN}OK{RESET}"
            )
            print(
                f"[{done_count:3d}/{total}] {status} "
                f"{r['category']:20s} | {r['time_s']:5.1f}s | "
                f"{r['question'][:55]}"
            )

    await asyncio.gather(*[_worker(i, item) for i, item in enumerate(items)])
    return results


def main():
    parser = argparse.ArgumentParser(description="Check agrinet responses for JSON/code leakage")
    parser.add_argument("--sample", type=int, default=15, help="Number of random samples (default: 15)")
    parser.add_argument("--concurrency", type=int, default=2, help="Max parallel requests (default: 2)")
    parser.add_argument("--out", type=str, default="tests/results_json_check.json", help="Output file")
    args = parser.parse_args()

    items = json.loads(QUESTIONS_PATH.read_text())

    if args.sample and args.sample < len(items):
        items = random.sample(items, args.sample)

    print(f"Running {len(items)} questions, checking for JSON/code leakage...\n")

    results = asyncio.run(run_all(items, concurrency=args.concurrency))

    # Summary
    flagged = [r for r in results if r["json_findings"]]
    errors = [r for r in results if r["error"]]
    clean = [r for r in results if not r["json_findings"] and not r["error"]]

    print(f"\n{'=' * 70}")
    print(f"JSON/CODE LEAKAGE REPORT")
    print(f"{'=' * 70}")
    print(f"  Total:   {len(results)}")
    print(f"  Clean:   {GREEN}{len(clean)}{RESET}")
    print(f"  Flagged: {YELLOW}{len(flagged)}{RESET}")
    print(f"  Errors:  {RED}{len(errors)}{RESET}")

    if flagged:
        print(f"\n{'=' * 70}")
        print(f"FLAGGED RESPONSES")
        print(f"{'=' * 70}")
        for r in flagged:
            print(f"\n  Q: {r['question'][:70]}")
            print(f"  Category: {r['category']} | Lang: {r['language']}")
            print(f"  Findings:")
            for name, match in r["json_findings"]:
                print(f"    - {YELLOW}{name}{RESET}: {match[:80]}")
            print(f"  Response (first 300 chars):")
            print(f"    {r['response'][:300]}")

    if errors:
        print(f"\n{'=' * 70}")
        print(f"ERRORS")
        print(f"{'=' * 70}")
        for r in errors:
            print(f"  [{r['category']}] {r['question'][:60]}")
            print(f"    {r['error']}")

    # Save results (without findings tuples for JSON serialization)
    save_results = []
    for r in results:
        save_r = {k: v for k, v in r.items() if k != "json_findings"}
        save_r["json_findings"] = [{"type": name, "match": match} for name, match in r["json_findings"]]
        save_results.append(save_r)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(save_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
