"""Run the benchmark questions through the production moderation -> agrinet pipeline.

Mandi questions get a second turn: the current prompt makes the agent stop and ask
"today's price, or a specific date?" before touching any mandi tool, so a single-turn
run would never exercise the mandi tool path at all. Turn 2 answers "today's price"
in the question's own language and the judged answer is the turn-2 response.

Usage:
    .venv/bin/python3 -m benchmarks.voice_quality.run_bench
    .venv/bin/python3 -m benchmarks.voice_quality.run_bench --concurrency 4 --limit 5
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# marqo.Client builds request paths by concatenation, so a trailing slash on the
# endpoint yields "//indexes/..." and every search 404s. Strip it before the tools
# module reads the variable.
if os.getenv("MARQO_ENDPOINT_URL"):
    os.environ["MARQO_ENDPOINT_URL"] = os.environ["MARQO_ENDPOINT_URL"].rstrip("/")

logging.disable(logging.CRITICAL)

from langcodes import Language  # noqa: E402
from pydantic_ai import Agent  # noqa: E402

from agents.agrinet import agrinet_agent  # noqa: E402
from agents.deps import FarmerContext  # noqa: E402
from agents.models import AGRINET_MODEL, MODERATION_MODEL  # noqa: E402
from agents.moderation import moderation_agent  # noqa: E402
from app.utils import filter_thinking_from_history  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

MANDI_TOOLS = {"get_mandi_prices", "search_commodity"}

# Turn-2 reply used to satisfy the mandi date clarification, per language.
TODAYS_PRICE = {
    "as": "আজিৰ দাম",
    "bn": "আজকের দাম",
    "en": "Today's price",
    "gu": "આજનો ભાવ",
    "hi": "आज का भाव",
    "kn": "ಇಂದಿನ ಬೆಲೆ",
    "ml": "ഇന്നത്തെ വില",
    "mr": "आजचा भाव",
    "ta": "இன்றைய விலை",
    "te": "ఈ రోజు ధర",
}


def moderation_prompt(question: str, lang_code: str) -> str:
    return f'**User:** "{question}"\n**Selected Language:** {Language.get(lang_code).display_name()}'


# Tool payloads are only used as judge context, so cap them to keep the judge prompt
# inside a sane token budget (a single Marqo hit can be 20k+ characters).
MAX_TOOL_OUTPUT_CHARS = 4000


def collect_tool_activity(messages) -> tuple[list[str], list[dict]]:
    """Return (tool_names, [{tool, args, output}]) for one turn."""
    names: list[str] = []
    activity: list[dict] = []
    pending: dict[str, dict] = {}

    for message in messages:
        for part in message.parts:
            kind = getattr(part, "part_kind", "")
            if kind == "tool-call":
                names.append(part.tool_name)
                entry = {
                    "tool": part.tool_name,
                    "args": str(getattr(part, "args", ""))[:600],
                    "output": "",
                }
                activity.append(entry)
                pending[part.tool_call_id] = entry
            elif kind == "tool-return":
                entry = pending.get(part.tool_call_id)
                if entry is not None:
                    entry["output"] = str(getattr(part, "content", ""))[:MAX_TOOL_OUTPUT_CHARS]
            elif kind == "retry-prompt":
                entry = pending.get(getattr(part, "tool_call_id", None))
                if entry is not None:
                    entry["output"] = f"[tool retry] {str(getattr(part, 'content', ''))[:600]}"

    return names, activity


async def run_turn(question: str, lang_code: str, session_id: str, history: list):
    """One moderation + agrinet turn. Returns (answer, tools, activity, all_messages)."""
    moderation = await moderation_agent.run(moderation_prompt(question, lang_code))
    deps = FarmerContext(query=question, lang_code=lang_code, session_id=session_id)
    deps.update_moderation_str(str(moderation.output))

    async with agrinet_agent.iter(
        user_prompt=deps.get_user_message(),
        message_history=history,
        deps=deps,
    ) as run:
        async for node in run:
            if Agent.is_end_node(node):
                break

    if not run.result:
        return "", [], [], history
    tools, activity = collect_tool_activity(run.result.new_messages())
    return (
        run.result.output or "",
        tools,
        activity,
        filter_thinking_from_history(run.result.all_messages()),
    )


async def run_item(item: dict) -> dict:
    lang = item["language"]
    session_id = f"vq_{lang}_{item['session_id']}"
    started = time.perf_counter()

    record = {
        **item,
        "turns": [],
        "tool_calls": [],
        "tool_activity": [],
        "answer": "",
        "elapsed_seconds": 0.0,
        "error": None,
    }

    try:
        answer, tools, activity, history = await run_turn(item["question"], lang, session_id, [])
        record["turns"].append({"role": "user", "text": item["question"]})
        record["turns"].append({"role": "bot", "text": answer, "tool_calls": tools})
        record["tool_calls"].extend(tools)
        record["tool_activity"].extend(activity)
        record["answer"] = answer

        # Mandi date clarification: the agent is required to ask before calling any
        # mandi tool, so give it the answer and judge the turn that follows.
        needs_second_turn = (
            item["category"] == "mandi" and not MANDI_TOOLS.intersection(tools)
        )
        if needs_second_turn:
            follow_up = TODAYS_PRICE[lang]
            answer2, tools2, activity2, _ = await run_turn(follow_up, lang, session_id, history)
            record["turns"].append({"role": "user", "text": follow_up})
            record["turns"].append({"role": "bot", "text": answer2, "tool_calls": tools2})
            record["tool_calls"].extend(tools2)
            record["tool_activity"].extend(activity2)
            record["answer"] = answer2
            record["date_clarification_needed"] = True
        else:
            record["date_clarification_needed"] = False

    except Exception as exc:  # noqa: BLE001 - benchmark records failures rather than aborting
        record["error"] = f"{type(exc).__name__}: {exc}"

    record["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    record["word_count"] = len(record["answer"].split())
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="only run the first N items")
    parser.add_argument("--out", default=str(DATA_DIR / "responses.json"))
    args = parser.parse_args()

    dataset = json.loads((DATA_DIR / "dataset.json").read_text(encoding="utf-8"))
    items = dataset["items"][: args.limit] if args.limit else dataset["items"]

    print(f"agrinet deployment : {AGRINET_MODEL.model_name}")
    print(f"moderation deployment: {MODERATION_MODEL.model_name}")
    print(f"running {len(items)} items at concurrency {args.concurrency}\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict | None] = [None] * len(items)
    done = 0

    async def worker(index: int, item: dict) -> None:
        nonlocal done
        async with semaphore:
            record = await run_item(item)
            results[index] = record
            done += 1
            status = "ERR" if record["error"] else " ok"
            tools = ",".join(record["tool_calls"][:3]) or "none"
            print(
                f"[{done:3d}/{len(items)}] {status} {record['language']}/{record['session_id']:<4} "
                f"{record['category']:<13} {record['elapsed_seconds']:6.1f}s "
                f"turns={len(record['turns']) // 2} tools={tools[:45]:<45} "
                f"{record['question_en'][:45]}",
                flush=True,
            )

    await asyncio.gather(*(worker(i, item) for i, item in enumerate(items)))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = [r for r in results if r and r["error"]]
    no_tools = [r for r in results if r and not r["tool_calls"] and not r["error"]]
    print(f"\nwrote {out_path}")
    print(f"errors: {len(errors)} | answers with no tool call: {len(no_tools)}")


if __name__ == "__main__":
    asyncio.run(main())
