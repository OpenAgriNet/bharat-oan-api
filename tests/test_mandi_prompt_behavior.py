"""
Targeted behavioral tests for mandi prompt changes:
  1. Date already stated  → no re-ask, proceed to tools
  2. No date in query     → hard stop, ask for date clarification

Usage:
    python3 -m tests.test_mandi_prompt_behavior
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agents.agrinet import agrinet_agent
from agents.deps import FarmerContext
from pydantic_ai import Agent

OK   = "\033[92m PASS \033[0m"
FAIL = "\033[91m FAIL \033[0m"

# (description, query, lang_code, expect_date_reask, expect_tool_attempt)
# expect_date_reask: True = model should ask for date clarification
# expect_tool_attempt: True = model should attempt mandi tools (forward_geocode / search_commodity)
CASES = [
    (
        "EN – 'today' + city+state → no date re-ask, tools attempted",
        "What is today's price of onion in Pune, Maharashtra?",
        "en", False, True,
    ),
    (
        "HI – 'aaj' + city+state → no date re-ask, tools attempted",
        "आज पुणे, महाराष्ट्र में प्याज का भाव क्या है?",
        "hi", False, True,
    ),
    (
        "EN – no date → must ask for date, no tool calls",
        "What is the price of onion in Pune, Maharashtra?",
        "en", True, False,
    ),
    (
        "HI – no date → must ask for date, no tool calls",
        "पुणे, महाराष्ट्र में प्याज का भाव क्या है?",
        "hi", True, False,
    ),
]

DATE_REASK_PHRASES = [
    "would you like today",
    "today's price or",
    "specific date",
    "क्या आप आज का भाव",
    "कोई विशिष्ट तारीख",
    "आप कौन-सी तारीख",
    "तारीख की पुष्टि",
    "तारीख बता",
]

MANDI_TOOLS = {"forward_geocode", "search_commodity", "get_mandi_prices"}


async def run_case(desc, query, lang_code, expect_date_reask, expect_tool_attempt):
    deps = FarmerContext(query=query, lang_code=lang_code, session_id="test-prompt-behavior")
    deps.update_moderation_str("category=mandi")
    user_message = deps.get_user_message()

    tools_called = []
    response_text = ""

    async with agrinet_agent.iter(
        user_prompt=user_message,
        message_history=[],
        deps=deps,
    ) as run:
        async for node in run:
            if Agent.is_call_tools_node(node):
                continue
            elif Agent.is_end_node(node):
                break

    if run.result:
        response_text = (run.result.output or "").lower()
        for msg in run.result.new_messages():
            for part in msg.parts:
                if getattr(part, "part_kind", "") == "tool-call":
                    tools_called.append(part.tool_name)

    mandi_tools_used = [t for t in tools_called if t in MANDI_TOOLS]
    reask_detected = any(p in response_text for p in DATE_REASK_PHRASES)

    reask_ok = reask_detected == expect_date_reask
    tool_ok = bool(mandi_tools_used) == expect_tool_attempt

    passed = reask_ok and tool_ok
    status = OK if passed else FAIL
    print(f"{status} {desc}")
    if not passed:
        if not reask_ok:
            print(f"       date re-ask: expected={'yes' if expect_date_reask else 'no'}, got={'yes' if reask_detected else 'no'}")
        if not tool_ok:
            print(f"       tools: expected={'attempted' if expect_tool_attempt else 'none'}, got={mandi_tools_used or 'none'}")
        print(f"       response: {response_text[:250]}")

    return passed


async def main():
    results = []
    for case in CASES:
        passed = await run_case(*case)
        results.append(passed)

    total = len(results)
    passed = sum(results)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
