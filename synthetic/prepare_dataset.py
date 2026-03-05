"""
Collect valid synthetic conversations and prepare a Hugging Face dataset.

Reads all JSONL files from the synthetic data directory, filters out errored
conversations, converts pydantic-ai message histories into a clean chat format,
and saves as a HF Dataset (Parquet + dataset card).

Usage:
    python -m synthetic.prepare_dataset [--data-dir data/synthetic] [--output-dir data/hf_dataset]
"""

import argparse
import json
from pathlib import Path

from datasets import Dataset, Features, Value


DATA_DIR = Path("data/synthetic")
OUTPUT_DIR = Path("data/hf_dataset")

# ── pydantic-ai part_kind → role mapping ────────────────────────────────
ROLE_MAP = {
    "user-prompt": "user",
    "text": "assistant",
    "tool-call": "tool_call",
    "tool-return": "tool_result",
    "thinking": "thinking",
}


def _extract_messages(raw_messages_json: str) -> list[dict]:
    """Convert pydantic-ai message history JSON into a flat chat message list.

    Each output dict has keys: role, content, and optionally tool_name /
    tool_call_id / tool_args for tool interactions.

    Filters out:
    - retry-prompt messages (pydantic-ai internal retries)
    - empty thinking parts (model produced thinking without text)
    - empty text parts (no actual content)
    """
    raw_messages = json.loads(raw_messages_json)
    messages: list[dict] = []

    for msg in raw_messages:
        for part in msg.get("parts", []):
            kind = part.get("part_kind", "")

            # Skip retry-prompts (internal pydantic-ai retries)
            if kind == "retry-prompt":
                continue

            role = ROLE_MAP.get(kind)
            if role is None:
                continue

            # Skip empty thinking or text parts
            if kind in ("thinking", "text") and not str(part.get("content", "")).strip():
                continue

            entry: dict = {"role": role}

            if kind == "tool-call":
                entry["content"] = ""
                entry["tool_name"] = part.get("tool_name", "")
                entry["tool_call_id"] = part.get("tool_call_id", "")
                entry["tool_args"] = (
                    part["args"]
                    if isinstance(part.get("args"), str)
                    else json.dumps(part.get("args", {}), ensure_ascii=False)
                )
            elif kind == "tool-return":
                entry["content"] = str(part.get("content", ""))
                entry["tool_name"] = part.get("tool_name", "")
                entry["tool_call_id"] = part.get("tool_call_id", "")
                entry["tool_args"] = ""
            else:
                entry["content"] = str(part.get("content", ""))
                entry["tool_name"] = ""
                entry["tool_call_id"] = ""
                entry["tool_args"] = ""

            messages.append(entry)

    return messages


def load_records(data_dir: Path) -> list[dict]:
    """Load all valid (non-error, turn_count > 0) records from JSONL files."""
    records: list[dict] = []
    total = 0
    skipped = 0

    for jsonl_file in sorted(data_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                rec = json.loads(line)

                # Skip errored or empty conversations
                if rec.get("error") or rec.get("turn_count", 0) == 0:
                    skipped += 1
                    continue

                records.append(rec)

    print(f"Loaded {len(records)} valid records from {total} total ({skipped} skipped)")
    return records


def record_to_row(rec: dict) -> dict:
    """Transform a raw conversation record into a flat dataset row."""
    env = rec.get("env", {})
    scenario = rec.get("profile", {}).get("scenario", {})

    messages = _extract_messages(rec.get("agrinet_messages_json", "[]"))

    profile = rec.get("profile", {})

    return {
        "session_id": rec["session_id"],
        "target_language": env.get("target_language", ""),
        "user_language": profile.get("language", ""),
        "today_date": str(env.get("today_date", "")),
        "scenario_id": scenario.get("id", ""),
        "scenario_category": scenario.get("category", ""),
        "scenario_description": scenario.get("description", ""),
        "turn_count": rec.get("turn_count", 0),
        "completed": rec.get("completed", False),
        "messages": json.dumps(messages, ensure_ascii=False),
    }


def build_dataset(rows: list[dict]) -> Dataset:
    """Build a HF Dataset from processed rows."""
    features = Features({
        "session_id": Value("string"),
        "target_language": Value("string"),
        "user_language": Value("string"),
        "today_date": Value("string"),
        "scenario_id": Value("string"),
        "scenario_category": Value("string"),
        "scenario_description": Value("string"),
        "turn_count": Value("int32"),
        "completed": Value("bool"),
        "messages": Value("large_string"),
    })

    columns: dict[str, list] = {k: [] for k in features}
    for row in rows:
        for k in features:
            columns[k].append(row[k])

    return Dataset.from_dict(columns, features=features)


def main():
    parser = argparse.ArgumentParser(description="Prepare HF dataset from synthetic conversations.")
    parser.add_argument("--data-dir", type=str, default=str(DATA_DIR), help="Input JSONL directory")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR), help="Output dataset directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)

    # Load and filter
    records = load_records(data_dir)
    if not records:
        print("No valid records found. Exiting.")
        return

    # Transform
    print("Transforming records...")
    rows = [record_to_row(r) for r in records]

    # Build dataset
    print("Building HF Dataset...")
    ds = build_dataset(rows)
    print(ds)

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    ds.save_to_disk(str(output_dir))
    print(f"\nDataset saved to {output_dir}")

    # Also save as parquet for easy sharing
    parquet_path = output_dir / "data.parquet"
    ds.to_parquet(str(parquet_path))
    print(f"Parquet saved to {parquet_path}")


if __name__ == "__main__":
    main()
