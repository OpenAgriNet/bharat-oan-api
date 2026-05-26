"""
Test for content leakage during streaming — mirrors the production
streaming path in app/services/chat.py exactly.

Captures every chunk that would be yielded to the user and checks for:
  - Raw JSON objects
  - Tool parameter keys
  - Thinking traces (<think> / </think>)
  - Tool call syntax (function_name(...))
  - Tool result data (raw API responses)
  - Reasoning markers

Usage:
    .venv/bin/python3 -m tests.check_streaming_leakage
    .venv/bin/python3 -m tests.check_streaming_leakage --sample 10
    .venv/bin/python3 -m tests.check_streaming_leakage --concurrency 8
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
from pydantic_ai import (
    AgentRunResultEvent,
    FinalResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.messages import TextPart, ThinkingPart
from langcodes import Language

QUESTIONS_PATH = ROOT / "tests" / "fixtures" / "agrinet_single_turn.json"
LANG_CODE_MAP = {"en": "en", "hi": "hi"}

# Patterns that indicate leakage in streamed chunks
LEAKAGE_PATTERNS = [
    (re.compile(r'\{[^}]*"[^"]+"\s*:'), "JSON object"),
    (re.compile(r'"(term|threshold|query|scheme_name|phone_number|latitude|longitude|max_results|top_k|place_name|commodity_code|days_back|inquiry_type|season|cycle|reg_no|otp|identity_no|grievance_type|grievance_description)"\s*:'), "tool param key"),
    (re.compile(r'<think>'), "thinking trace start"),
    (re.compile(r'</think>'), "thinking trace end"),
    (re.compile(r'(search_terms|search_documents|search_pests_diseases|search_videos|forward_geocode|reverse_geocode|weather_forecast|get_mandi_prices|search_commodity|get_scheme_info|check_pmfby_status|check_shc_status|initiate_pm_kisan_status_check|check_pm_kisan_status_with_otp|submit_grievance|grievance_status)\s*\('), "raw function call"),
    (re.compile(r'```(json|python)'), "code block"),
    (re.compile(r'"tool_call"'), "tool_call marker"),
    (re.compile(r'"function"'), "function marker"),
    (re.compile(r'ToolCallPart|ToolReturnPart|ModelRequest|ModelResponse'), "pydantic-ai internal type"),
]

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def check_text(text: str) -> list[tuple[str, str]]:
    """Check text for leakage patterns."""
    findings = []
    for pattern, name in LEAKAGE_PATTERNS:
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


async def run_single_streaming(item: dict) -> dict:
    """Run a single question through the STREAMING path (mirrors chat.py exactly)."""
    question = item["question"]
    category = item["category"]
    lang = item.get("language", "en")
    lang_code = LANG_CODE_MAP.get(lang, lang)

    start = time.perf_counter()
    tools_called = []
    raw_streamed_chunks = []    # raw chunks before <think> stripping
    streamed_chunks = []        # every chunk that would be yielded to user (after <think> strip)
    pre_final_chunks = []       # text deltas BEFORE FinalResultEvent (should be empty)
    thinking_chunks = []        # thinking deltas (should NOT reach user)
    all_events_log = []         # log of all event types for debugging
    inside_leaked_think = False # track leaked <think> blocks across chunks

    try:
        # Step 1: Moderation
        mod_message = format_moderation_message(item)
        mod_result = await moderation_agent.run(mod_message)
        mod_output = mod_result.output

        # Step 2: Agent streaming — mirrors chat.py lines 76-110
        deps = FarmerContext(
            query=question,
            lang_code=lang_code,
            session_id=f"test_stream_{category}_{id(item)}",
        )
        deps.update_moderation_str(str(mod_output))
        user_message = deps.get_user_message()

        final_result_found = False

        async for event in agrinet_agent.run_stream_events(
            user_prompt=user_message,
            message_history=[],
            deps=deps,
        ):
            kind = getattr(event, 'event_kind', '')

            if kind == 'part_start':
                if isinstance(event.part, ThinkingPart):
                    all_events_log.append("part_start:thinking")
                elif isinstance(event.part, TextPart):
                    all_events_log.append("part_start:text")
                else:
                    all_events_log.append(f"part_start:{type(event.part).__name__}")

            elif kind == 'part_delta':
                if isinstance(event.delta, ThinkingPartDelta):
                    all_events_log.append("delta:thinking")
                    thinking_chunks.append(event.delta.content_delta or "")
                elif isinstance(event.delta, TextPartDelta):
                    chunk = event.delta.content_delta or ""
                    if final_result_found:
                        all_events_log.append("delta:text:YIELDED")
                        raw_streamed_chunks.append(chunk)

                        # Apply same <think> stripping
                        if inside_leaked_think:
                            end_idx = chunk.find('</think>')
                            if end_idx >= 0:
                                inside_leaked_think = False
                                chunk = chunk[end_idx + len('</think>'):]
                            else:
                                chunk = ""
                        if '<think>' in chunk:
                            end_idx = chunk.find('</think>')
                            if end_idx >= 0:
                                chunk = re.sub(r'<think>[\s\S]*?</think>', '', chunk)
                            else:
                                inside_leaked_think = True
                                chunk = chunk[:chunk.find('<think>')]

                        if chunk:
                            streamed_chunks.append(chunk)
                    else:
                        all_events_log.append("delta:text:PRE_FINAL")
                        pre_final_chunks.append(chunk)
                else:
                    all_events_log.append(f"delta:{type(event.delta).__name__}")

            elif kind == 'final_result':
                all_events_log.append("FINAL_RESULT_EVENT")
                final_result_found = True

            elif kind == 'function_tool_call':
                all_events_log.append("function_tool_call")
                tool_name = getattr(event.part, 'tool_name', None)
                if tool_name:
                    tools_called.append(tool_name)

            elif kind == 'function_tool_result':
                all_events_log.append("function_tool_result")
                final_result_found = False  # Reset for next model turn

            elif kind == 'agent_run_result':
                all_events_log.append("agent_run_result")

        elapsed = time.perf_counter() - start

        # Assemble full streamed text
        full_raw_streamed = "".join(raw_streamed_chunks)
        # Apply regex stripping on the full assembled text (simulates chat.py fix)
        full_streamed_filtered = re.sub(r'<think>[\s\S]*?</think>', '', full_raw_streamed)
        # Also strip any unclosed <think> at the end
        full_streamed_filtered = re.sub(r'<think>[\s\S]*$', '', full_streamed_filtered)
        full_streamed = full_streamed_filtered.strip()
        full_pre_final = "".join(pre_final_chunks)
        full_thinking = "".join(thinking_chunks)

        # Check for leakage in the filtered stream (what user sees after fix)
        stream_findings = check_text(full_streamed)
        # Also check raw stream to see what WOULD have leaked without the fix
        raw_stream_findings = check_text(full_raw_streamed)

        # Check if pre-final chunks leaked anything (they shouldn't reach user in chat.py)
        pre_final_findings = check_text(full_pre_final) if full_pre_final else []

        # Check if thinking content would have leaked without the filter
        thinking_findings = check_text(full_thinking) if full_thinking else []

        return {
            "question": question,
            "category": category,
            "language": lang,
            "tools": tools_called,
            "streamed_response": full_streamed,
            "streamed_chunk_count": len(streamed_chunks),
            "raw_streamed_response": full_raw_streamed,
            "raw_stream_findings": raw_stream_findings,
            "pre_final_text": full_pre_final if full_pre_final else None,
            "pre_final_chunk_count": len(pre_final_chunks),
            "thinking_present": bool(thinking_chunks),
            "thinking_length": len(full_thinking),
            "stream_findings": stream_findings,
            "pre_final_findings": pre_final_findings,
            "thinking_findings": thinking_findings,
            "event_summary": _summarize_events(all_events_log),
            "time_s": round(elapsed, 1),
            "error": None,
        }

    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "question": question,
            "category": category,
            "language": lang,
            "tools": tools_called,
            "streamed_response": "".join(streamed_chunks),
            "streamed_chunk_count": len(streamed_chunks),
            "pre_final_text": "".join(pre_final_chunks) if pre_final_chunks else None,
            "pre_final_chunk_count": len(pre_final_chunks),
            "thinking_present": bool(thinking_chunks),
            "thinking_length": len("".join(thinking_chunks)),
            "stream_findings": [],
            "pre_final_findings": [],
            "thinking_findings": [],
            "event_summary": _summarize_events(all_events_log),
            "time_s": round(elapsed, 1),
            "error": f"{type(e).__name__}: {e}",
        }


def _summarize_events(events: list[str]) -> dict:
    """Summarize event log into counts."""
    summary = {}
    for e in events:
        summary[e] = summary.get(e, 0) + 1
    return summary


async def run_all(items: list[dict], concurrency: int = 4) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    results = [None] * len(items)
    done_count = 0
    total = len(items)

    async def _worker(idx, item):
        nonlocal done_count
        async with sem:
            r = await run_single_streaming(item)
            results[idx] = r
            done_count += 1

            # Status indicator
            issues = []
            if r["error"]:
                issues.append(f"{RED}ERR{RESET}")
            if r["stream_findings"]:
                issues.append(f"{RED}LEAK{RESET}")
            if r["pre_final_text"]:
                issues.append(f"{YELLOW}PRE-FINAL{RESET}")
            if r["thinking_present"]:
                issues.append(f"{CYAN}THINK{RESET}")

            status = ", ".join(issues) if issues else f"{GREEN}OK{RESET}"
            print(
                f"[{done_count:3d}/{total}] {status:40s} "
                f"{r['category']:20s} | {r['time_s']:5.1f}s | "
                f"chunks:{r['streamed_chunk_count']:4d} | "
                f"{r['question'][:45]}"
            )

    await asyncio.gather(*[_worker(i, item) for i, item in enumerate(items)])
    return results


def main():
    parser = argparse.ArgumentParser(description="Check streaming for content leakage")
    parser.add_argument("--sample", type=int, default=0, help="Number of random samples (0 = all)")
    parser.add_argument("--concurrency", type=int, default=16, help="Max parallel requests")
    parser.add_argument("--out", type=str, default="tests/results_streaming_check.json", help="Output file")
    args = parser.parse_args()

    items = json.loads(QUESTIONS_PATH.read_text())

    if args.sample and args.sample < len(items):
        items = random.SystemRandom().sample(items, args.sample)

    print(f"Running {len(items)} questions via STREAMING path...\n")

    results = asyncio.run(run_all(items, concurrency=args.concurrency))

    # Summary
    leaked = [r for r in results if r["stream_findings"]]
    raw_leaked = [r for r in results if r.get("raw_stream_findings")]
    pre_final = [r for r in results if r["pre_final_text"]]
    thinking = [r for r in results if r["thinking_present"]]
    errors = [r for r in results if r["error"]]
    clean = [r for r in results if not r["stream_findings"] and not r["error"]]

    print(f"\n{'=' * 70}")
    print(f"STREAMING LEAKAGE REPORT")
    print(f"{'=' * 70}")
    print(f"  Total:              {len(results)}")
    print(f"  Clean (filtered):   {GREEN}{len(clean)}{RESET}")
    print(f"  Leaks AFTER fix:    {RED}{len(leaked)}{RESET}  (would reach user)")
    print(f"  Leaks BEFORE fix:   {YELLOW}{len(raw_leaked)}{RESET}  (caught by <think> strip)")
    print(f"  Pre-final text:     {YELLOW}{len(pre_final)}{RESET}  (text before FinalResultEvent)")
    print(f"  Thinking present:   {CYAN}{len(thinking)}{RESET}  (filtered, not streamed)")
    print(f"  Errors:             {RED}{len(errors)}{RESET}")

    if raw_leaked and not leaked:
        print(f"\n{'=' * 70}")
        print(f"{GREEN}THINK STRIP FIX WORKING — caught leaks that would have reached user{RESET}")
        print(f"{'=' * 70}")
        for r in raw_leaked:
            print(f"\n  Q: {r['question'][:70]}")
            print(f"  Category: {r['category']} | Lang: {r['language']}")
            print(f"  Raw findings (would have leaked):")
            for name, match in r["raw_stream_findings"]:
                print(f"    {YELLOW}{name}{RESET}: {match[:100]}")
            print(f"  Filtered response (clean, first 300 chars):")
            print(f"    {r['streamed_response'][:300]}")

    if leaked:
        print(f"\n{'=' * 70}")
        print(f"{RED}STREAM LEAKS — would reach the user!{RESET}")
        print(f"{'=' * 70}")
        for r in leaked:
            print(f"\n  Q: {r['question'][:70]}")
            print(f"  Category: {r['category']} | Lang: {r['language']}")
            for name, match in r["stream_findings"]:
                print(f"    {RED}{name}{RESET}: {match[:100]}")
            print(f"  Streamed text (first 500 chars):")
            print(f"    {r['streamed_response'][:500]}")

    if pre_final:
        print(f"\n{'=' * 70}")
        print(f"{YELLOW}PRE-FINAL TEXT — text deltas before FinalResultEvent{RESET}")
        print(f"{'=' * 70}")
        for r in pre_final:
            print(f"\n  Q: {r['question'][:70]}")
            print(f"  Category: {r['category']} | Pre-final chunks: {r['pre_final_chunk_count']}")
            pf_text = r['pre_final_text']
            pf_findings = r['pre_final_findings']
            if pf_findings:
                print(f"  {RED}Contains suspicious patterns:{RESET}")
                for name, match in pf_findings:
                    print(f"    {name}: {match[:100]}")
            print(f"  Pre-final text (first 300 chars):")
            print(f"    {pf_text[:300]}")

    if thinking:
        print(f"\n{'=' * 70}")
        print(f"{CYAN}THINKING TRACES (filtered correctly, not streamed){RESET}")
        print(f"{'=' * 70}")
        print(f"  {len(thinking)} questions had thinking content (avg {sum(r['thinking_length'] for r in thinking) / len(thinking):.0f} chars)")
        for r in thinking:
            print(f"    [{r['category']:20s}] {r['thinking_length']:6d} chars | {r['question'][:50]}")

    if errors:
        print(f"\n{'=' * 70}")
        print(f"ERRORS")
        print(f"{'=' * 70}")
        for r in errors:
            print(f"  [{r['category']}] {r['question'][:60]}")
            print(f"    {r['error']}")

    # Save results
    save_results = []
    for r in results:
        save_r = dict(r)
        save_r["stream_findings"] = [{"type": n, "match": m} for n, m in r["stream_findings"]]
        save_r["raw_stream_findings"] = [{"type": n, "match": m} for n, m in r.get("raw_stream_findings", [])]
        save_r["pre_final_findings"] = [{"type": n, "match": m} for n, m in r["pre_final_findings"]]
        save_r["thinking_findings"] = [{"type": n, "match": m} for n, m in r["thinking_findings"]]
        save_results.append(save_r)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(save_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
