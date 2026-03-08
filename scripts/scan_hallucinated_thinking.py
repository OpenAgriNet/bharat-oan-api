#!/usr/bin/env python3
"""
Scan synthetic JSONL files for hallucinated thinking traces.

Hallucinated thinking traces are model outputs where the thinking block contains
regurgitated document/search content instead of actual reasoning. These appear as
garbled fragments from prior context (search results, system prompts, tool returns)
and are reliably signaled by a retry-prompt message immediately following the
thinking message.

This script produces a report of all affected files without modifying anything.

Usage:
    python scripts/scan_hallucinated_thinking.py [--data-dir DATA_DIR] [--verbose]
"""

import argparse
import json
import os
import re
from collections import Counter


def get_content(part: dict) -> str:
    c = part.get("content", "")
    return str(c) if not isinstance(c, str) else c


def categorize_hallucination(content: str) -> str:
    """Categorize a hallucinated thinking trace into a sub-type."""
    content = content.strip()
    if not content:
        return "empty"

    if len(content) < 20:
        if content.isdigit():
            return "just_number"
        if not any(c.isascii() and c.isalpha() for c in content):
            return "short_indic_fragment"
        return "short_fragment"

    if "```" in content:
        return "search_result_with_codeblock"

    bracket_translations = re.findall(r"\[[^\]]{1,30}\]", content)
    if len(bracket_translations) >= 3:
        return "regurgitated_doc_with_translations"

    ascii_chars = sum(1 for c in content if c.isascii() and c.isalpha())
    total_alpha = sum(1 for c in content if c.isalpha())
    if total_alpha > 0 and ascii_chars / total_alpha < 0.3:
        return "indic_response_in_thinking"

    has_reasoning = bool(
        re.search(
            r"\b(I need to|The user|Let me|I should|I will|I can|I have|"
            r"First|Now,|This means|The question|They are|They want|"
            r"I found|Great!|From the|Based on)\b",
            content[:200],
            re.IGNORECASE,
        )
    )
    if not has_reasoning:
        return "english_doc_no_reasoning"

    return "other_with_reasoning"


def scan_file(fpath: str) -> list[dict]:
    """Scan a single JSONL file and return list of hallucination records."""
    findings = []
    try:
        with open(fpath) as f:
            data = json.loads(f.readline())
        agrinet_msgs = json.loads(data.get("agrinet_messages_json", "[]"))
    except (json.JSONDecodeError, KeyError):
        return findings

    for i in range(len(agrinet_msgs) - 1):
        parts = agrinet_msgs[i].get("parts", [])
        next_parts = agrinet_msgs[i + 1].get("parts", [])

        # Pattern: single-part thinking message followed by single-part retry-prompt
        if (
            len(parts) == 1
            and parts[0].get("part_kind") == "thinking"
            and len(next_parts) == 1
            and next_parts[0].get("part_kind") == "retry-prompt"
        ):
            content = get_content(parts[0])
            findings.append(
                {
                    "msg_idx": i,
                    "category": categorize_hallucination(content),
                    "content_len": len(content.strip()),
                    "content_preview": content.strip()[:200].replace("\n", " | "),
                    "provider": parts[0].get("provider_name", "N/A"),
                }
            )

    return findings


def main():
    parser = argparse.ArgumentParser(description="Scan for hallucinated thinking traces")
    parser.add_argument(
        "--data-dir",
        default="data/synthetic",
        help="Directory containing JSONL files (default: data/synthetic)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show individual hallucination details"
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    if not os.path.isdir(data_dir):
        print(f"Error: directory not found: {data_dir}")
        return

    total_files = 0
    files_affected = 0
    total_hallucinations = 0
    categories = Counter()
    providers = Counter()

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".jsonl"):
            continue
        total_files += 1
        findings = scan_file(os.path.join(data_dir, fname))

        if findings:
            files_affected += 1
            total_hallucinations += len(findings)

            for f in findings:
                categories[f["category"]] += 1
                providers[f["provider"]] += 1

            if args.verbose:
                for f in findings:
                    print(
                        f"  {fname} msg {f['msg_idx']} [{f['category']}] "
                        f"({f['content_len']} chars): {f['content_preview'][:100]}"
                    )

    print("=" * 60)
    print("HALLUCINATED THINKING TRACE SCAN REPORT")
    print("=" * 60)
    print(f"Files scanned:          {total_files}")
    print(f"Files affected:         {files_affected} ({files_affected * 100 // max(total_files, 1)}%)")
    print(f"Total hallucinations:   {total_hallucinations}")
    print()
    print("By category:")
    for cat, count in categories.most_common():
        pct = count * 100 // max(total_hallucinations, 1)
        print(f"  {cat:40s} {count:5d} ({pct}%)")
    print()
    print("By provider:")
    for prov, count in providers.most_common():
        print(f"  {prov:40s} {count:5d}")


if __name__ == "__main__":
    main()
