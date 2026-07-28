"""LLM-as-judge scoring for the voice-quality benchmark.

Scores each recorded response on the twelve rubric metrics used in the
Gemma comparison workbook, using a separate Azure deployment (gpt-5.4) from
the system under test (gpt-5.4-mini). Four further columns in that workbook
(output_hygiene, elapsed_seconds, word_count, error) are mechanical, not judged,
and are computed here directly.

The judge deployment is read from JUDGE_DEPLOYMENT_NAME and deliberately does not
fall back to AZURE_OPENAI_DEPLOYMENT_NAME - silently judging a model with itself
would invalidate the run.

Usage:
    JUDGE_DEPLOYMENT_NAME=gpt-5.4 .venv/bin/python3 -m benchmarks.voice_quality.judge
    ... --concurrency 6 --limit 5
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from langcodes import Language  # noqa: E402
from openai import AsyncAzureOpenAI  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

# metric -> (min, max). Scales mirror the Gemma comparison workbook exactly.
METRICS: dict[str, tuple[int, int]] = {
    "translation_accuracy": (1, 10),
    "tool_call_quality": (1, 4),
    "accuracy_completeness": (1, 4),
    "actionability": (0, 1),
    "conversation_closure": (0, 1),
    "source_data_comprehensiveness": (0, 1),
    "no_fabrication": (0, 1),
    "citation_accuracy": (0, 1),
    "citation_comprehensiveness": (1, 4),
    "grammar_fluency": (1, 4),
    "language_purity": (1, 4),
    "brevity": (1, 4),
}

RUBRIC = """You are grading a voice assistant for Indian farmers (Bharat Vistaar). The
assistant answers in the farmer's own language and its replies are read aloud by TTS,
so length and script purity matter as much as correctness.

Score each metric on its stated scale. Judge only what is in front of you: the answer
must be supported by the tool outputs shown, and if no tool output is shown, any
specific figure, date, price or scheme detail in the answer is fabricated.

1. translation_accuracy (1-10) - Is the reply natural, fluent, idiomatic {language},
   with correct agricultural terminology? 10 = reads as though written by a native
   speaker; 5 = understandable but reads like machine translation; 1 = wrong language,
   or meaning distorted.
2. tool_call_quality (1-4) - Were the right tools called, in a sensible order, with
   well-formed arguments? 4 = correct and efficient; 3 = correct but redundant or
   round-about; 2 = wrong tool, or a needed tool was skipped; 1 = no tool called when
   real-time data (price, weather, scheme, pest) was required.
3. accuracy_completeness (1-4) - Does the reply accurately and fully answer the
   question that was asked? 4 = fully and correctly; 1 = wrong or non-responsive.
4. actionability (0/1) - 1 only if the reply gives a concrete next step, dose, timing
   or threshold the farmer can act on. A bare fact or price is 0.
5. conversation_closure (0/1) - 1 if the reply ends with a specific, relevant
   follow-up question. A generic "anything else?" is 0.
6. source_data_comprehensiveness (0/1) - 1 if the tool outputs shown actually contained
   enough information to answer the question. 0 if they were empty, irrelevant or thin.
   This grades the retrieved data, not the model.
7. no_fabrication (0/1) - 1 only if every specific claim (numbers, dates, names,
   prices, dosages) traces to the tool outputs shown. Any invented specific = 0.
8. citation_accuracy (0/1) - 1 if a bold **Source: ...** line is present and names the
   source the data actually came from. Missing or mismatched citation = 0.
9. citation_comprehensiveness (1-4) - How well the cited sources cover the claims made.
   4 = every substantive claim is covered; 1 = claims are largely uncited.
10. grammar_fluency (1-4) - Grammatical correctness and natural phrasing in {language}.
11. language_purity (1-4) - Is the reply wholly in {language} and its native script?
    Deduct for Latin-script words, English numerals, or untranslated English terms
    where a common native term exists. 4 = pure; 1 = heavily mixed.
12. brevity (1-4) - Could this be spoken in about 30 seconds (roughly 80 words)?
    4 = concise; 3 = slightly long; 2 = clearly too long; 1 = a wall of text or tables.

Reply with JSON only, no markdown fence:
{{"metric_name": {{"score": <number>, "reason": "<one or two sentences>"}}, ...}}
Include all twelve metrics."""


def build_judge_prompt(record: dict) -> str:
    language = Language.get(record["language"]).display_name()

    if record["tool_activity"]:
        tool_block = "\n\n".join(
            f"TOOL: {a['tool']}\nARGS: {a['args']}\nOUTPUT:\n{a['output'] or '(empty)'}"
            for a in record["tool_activity"]
        )
    else:
        tool_block = "(no tools were called)"

    transcript = "\n".join(
        f"{'FARMER' if t['role'] == 'user' else 'ASSISTANT'}: {t['text']}"
        for t in record["turns"]
    )

    return (
        f"TARGET LANGUAGE: {language}\n"
        f"QUESTION (English gloss): {record['question_en']}\n"
        f"CATEGORY: {record['category']}\n\n"
        f"--- CONVERSATION ---\n{transcript}\n\n"
        f"--- TOOL CALLS AND OUTPUTS ---\n{tool_block}\n\n"
        f"--- ANSWER UNDER TEST (the final ASSISTANT turn) ---\n{record['answer']}\n"
    )


def parse_scores(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in judge reply: {raw[:200]}")
    return json.loads(text[start : end + 1])


def clamp(metric: str, value) -> float | None:
    low, high = METRICS[metric]
    try:
        return float(min(max(float(value), low), high))
    except (TypeError, ValueError):
        return None


# Streaming/thinking artifacts that should never reach a farmer.
HYGIENE_PATTERNS = re.compile(
    r"<think|</think|<\|.*?\|>|\bfunction_call\b|\btool_call\b|^\s*\{\s*\"", re.IGNORECASE
)


async def judge_one(client: AsyncAzureOpenAI, deployment: str, record: dict) -> dict:
    language = Language.get(record["language"]).display_name()
    result = {
        "session_id": record["session_id"],
        "language": record["language"],
        "category": record["category"],
        "question": record["question"],
        "question_en": record["question_en"],
        "answer": record["answer"],
        "tool_calls": ",".join(record["tool_calls"]),
        "turn_count": len(record["turns"]) // 2,
        "date_clarification_needed": record.get("date_clarification_needed", False),
        # Mechanical columns - computed, not judged.
        "score_output_hygiene": 0.0 if HYGIENE_PATTERNS.search(record["answer"] or "") else 1.0,
        "score_elapsed_seconds": record["elapsed_seconds"],
        "score_word_count": float(record.get("word_count", len((record["answer"] or "").split()))),
        "score_error": 1.0 if record["error"] else 0.0,
        "judge_error": None,
    }

    if record["error"]:
        result["judge_error"] = f"skipped, pipeline error: {record['error']}"
        for metric in METRICS:
            result[f"score_{metric}"] = None
            result[f"reason_{metric}"] = "not judged - pipeline error"
        return result

    system_prompt = RUBRIC.format(language=language)
    try:
        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_judge_prompt(record)},
            ],
            response_format={"type": "json_object"},
        )
        scores = parse_scores(response.choices[0].message.content or "")
    except Exception as exc:  # noqa: BLE001 - record and continue
        result["judge_error"] = f"{type(exc).__name__}: {exc}"
        for metric in METRICS:
            result[f"score_{metric}"] = None
            result[f"reason_{metric}"] = "not judged - judge error"
        return result

    for metric in METRICS:
        entry = scores.get(metric) or {}
        if not isinstance(entry, dict):
            entry = {"score": entry, "reason": ""}
        result[f"score_{metric}"] = clamp(metric, entry.get("score"))
        result[f"reason_{metric}"] = str(entry.get("reason", ""))[:1000]
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--responses", default=str(DATA_DIR / "responses.json"))
    parser.add_argument("--out", default=str(DATA_DIR / "scores.json"))
    args = parser.parse_args()

    deployment = os.getenv("JUDGE_DEPLOYMENT_NAME")
    if not deployment:
        raise SystemExit("JUDGE_DEPLOYMENT_NAME must be set (e.g. gpt-5.4)")
    if deployment == os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"):
        raise SystemExit(
            f"judge deployment {deployment!r} is the same as the model under test; "
            "use a different deployment"
        )

    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/"),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    )

    records = json.loads(Path(args.responses).read_text(encoding="utf-8"))
    records = records[: args.limit] if args.limit else records
    print(f"judging {len(records)} responses with deployment {deployment!r}\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict | None] = [None] * len(records)
    done = 0

    async def worker(index: int, record: dict) -> None:
        nonlocal done
        async with semaphore:
            scored = await judge_one(client, deployment, record)
            results[index] = scored
            done += 1
            status = "ERR" if scored["judge_error"] else " ok"
            tq = scored.get("score_tool_call_quality")
            ta = scored.get("score_translation_accuracy")
            print(
                f"[{done:3d}/{len(records)}] {status} {scored['language']}/{scored['session_id']:<4} "
                f"{scored['category']:<13} tool={tq if tq is not None else '-':<4} "
                f"trans={ta if ta is not None else '-':<5} {scored['question_en'][:45]}",
                flush=True,
            )

    await asyncio.gather(*(worker(i, r) for i, r in enumerate(records)))
    await client.close()

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results if r and r["judge_error"]]
    print(f"\nwrote {out_path} | judge failures: {len(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
