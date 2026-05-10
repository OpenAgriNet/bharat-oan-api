#!/usr/bin/env python3
"""Offline CLI for the training-data export adapter.

Reads a JSON file containing a list of trace dicts (e.g. dumped from
Langfuse via `langfuse.api.trace.list(...)` or any equivalent source) and
writes chat-template JSONL ready for `trl.SFTTrainer` /
`trl.DPOTrainer`.

Examples
--------

SFT export from a trace dump::

    python scripts/export_training_data.py \\
        --input traces.json \\
        --output bharat-oan-sft.jsonl

DPO pairs from a separate preferences file::

    python scripts/export_training_data.py \\
        --input traces.json \\
        --dpo-pairs preferences.json \\
        --output bharat-oan-dpo.jsonl

The preferences file must be a JSON list of objects shaped like::

    {"trace_id": "trace-...", "chosen": "...", "rejected": "...",
     "metadata": {"annotator": "..."}}

Each entry is matched to the trace with the same id. Traces that have no
matching preference entry are skipped in DPO mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/export_training_data.py ...` from anywhere by
# putting the repo root on sys.path before the helper import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers.training_export import (  # noqa: E402  (intentional path setup above)
    build_dpo_pair,
    langfuse_trace_to_sft,
    to_jsonl_line,
)


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(
            f"{path}: expected a JSON array of objects, got "
            f"{type(data).__name__}"
        )
    return data


def _write_sft(traces: list[dict], out: Path, *, include_tool_calls: bool) -> int:
    written = 0
    with out.open("w", encoding="utf-8") as f:
        for trace in traces:
            record = langfuse_trace_to_sft(
                trace, include_tool_calls=include_tool_calls
            )
            if record is None:
                continue
            f.write(to_jsonl_line(record) + "\n")
            written += 1
    return written


def _write_dpo(traces: list[dict], prefs: list[dict], out: Path) -> int:
    by_id = {str(t.get("id") or t.get("trace_id")): t for t in traces}
    written = 0
    with out.open("w", encoding="utf-8") as f:
        for pref in prefs:
            trace_id = str(pref.get("trace_id") or "")
            trace = by_id.get(trace_id)
            if trace is None:
                print(
                    f"warning: skipping preference for unknown trace_id "
                    f"{trace_id!r}",
                    file=sys.stderr,
                )
                continue
            pair = build_dpo_pair(
                trace,
                chosen_output=pref["chosen"],
                rejected_output=pref["rejected"],
                metadata=pref.get("metadata"),
            )
            f.write(to_jsonl_line(pair) + "\n")
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert agent traces into SFT/DPO JSONL.",
    )
    parser.add_argument(
        "--input", required=True, type=Path,
        help="Path to a JSON file with a list of trace dicts.",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Destination JSONL file.",
    )
    parser.add_argument(
        "--dpo-pairs", type=Path, default=None,
        help="Optional preferences file. When set, output is DPO pairs "
             "instead of SFT records.",
    )
    parser.add_argument(
        "--no-tool-calls", action="store_true",
        help="Drop intermediate tool-call / tool-result messages from "
             "SFT records (no effect in DPO mode).",
    )
    args = parser.parse_args()

    traces = _load_json_array(args.input)

    if args.dpo_pairs is not None:
        prefs = _load_json_array(args.dpo_pairs)
        n = _write_dpo(traces, prefs, args.output)
        print(f"Wrote {n} DPO pairs to {args.output}")
    else:
        n = _write_sft(
            traces, args.output, include_tool_calls=not args.no_tool_calls
        )
        print(f"Wrote {n} SFT records to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
