#!/usr/bin/env python3
"""
Remove hallucinated thinking traces from synthetic JSONL files.

Hallucinated thinking traces are model outputs where the thinking block contains
regurgitated document/search content (search results, system prompt fragments,
Indic-language response text, garbled fragments) instead of actual reasoning.

Detection: A hallucinated thinking trace is reliably identified by the pattern
of a single-part thinking message immediately followed by a single-part
retry-prompt message ("Please return text or call a tool."). In 93%+ of cases
the model recovers with a correct thinking trace after the retry.

Cleanup strategy: Remove both the hallucinated thinking message and the
retry-prompt message. The good thinking message that follows is preserved,
so the conversation reads as if the model reasoned correctly on the first try.

The script handles chains (2-3 consecutive hallucination→retry cycles) by
iterating until no more pairs remain.

Usage:
    # Dry run (report only, no modifications):
    python scripts/clean_hallucinated_thinking.py --data-dir data/synthetic --dry-run

    # Modify files in-place:
    python scripts/clean_hallucinated_thinking.py --data-dir data/synthetic
"""

import argparse
import json
import os


def clean_messages(agrinet_msgs: list[dict]) -> tuple[list[dict], int]:
    """
    Remove hallucinated thinking → retry-prompt pairs from a message list.

    Iterates until stable to handle consecutive chains.

    Returns:
        (cleaned_messages, number_of_messages_removed)
    """
    total_removed = 0

    changed = True
    while changed:
        changed = False
        new_msgs = []
        skip_next = False

        for i in range(len(agrinet_msgs)):
            if skip_next:
                skip_next = False
                continue

            parts = agrinet_msgs[i].get("parts", [])

            # Check for pattern: single-part thinking followed by single-part retry-prompt
            if (
                i + 1 < len(agrinet_msgs)
                and len(parts) == 1
                and parts[0].get("part_kind") == "thinking"
            ):
                next_parts = agrinet_msgs[i + 1].get("parts", [])
                if (
                    len(next_parts) == 1
                    and next_parts[0].get("part_kind") == "retry-prompt"
                ):
                    skip_next = True
                    changed = True
                    total_removed += 2
                    continue

            new_msgs.append(agrinet_msgs[i])

        agrinet_msgs = new_msgs

    return agrinet_msgs, total_removed


def process_file(fpath: str, dry_run: bool = False) -> tuple[bool, int]:
    """
    Process a single JSONL file.

    Returns:
        (was_modified, messages_removed)
    """
    try:
        with open(fpath) as f:
            data = json.loads(f.readline())
        agrinet_msgs = json.loads(data.get("agrinet_messages_json", "[]"))
    except (json.JSONDecodeError, KeyError):
        return False, 0

    cleaned, removed = clean_messages(agrinet_msgs)

    if removed == 0:
        return False, 0

    if not dry_run:
        data["agrinet_messages_json"] = json.dumps(cleaned, ensure_ascii=False)
        with open(fpath, "w") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    return True, removed


def main():
    parser = argparse.ArgumentParser(
        description="Remove hallucinated thinking traces from synthetic JSONL files"
    )
    parser.add_argument(
        "--data-dir",
        default="data/synthetic",
        help="Directory containing JSONL files (default: data/synthetic)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be changed without modifying files",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    if not os.path.isdir(data_dir):
        print(f"Error: directory not found: {data_dir}")
        return

    total_files = 0
    files_modified = 0
    total_removed = 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".jsonl"):
            continue
        total_files += 1

        modified, removed = process_file(
            os.path.join(data_dir, fname), dry_run=args.dry_run
        )

        if modified:
            files_modified += 1
            total_removed += removed

    mode = "DRY RUN" if args.dry_run else "CLEANUP"
    print("=" * 60)
    print(f"HALLUCINATED THINKING TRACE {mode} REPORT")
    print("=" * 60)
    print(f"Files scanned:              {total_files}")
    print(f"Files {'would modify' if args.dry_run else 'modified':22s} {files_modified}")
    print(f"Messages {'would remove' if args.dry_run else 'removed':20s} {total_removed}")
    print(f"  Hallucinated thinking:    {total_removed // 2}")
    print(f"  Retry-prompts:            {total_removed // 2}")

    if args.dry_run:
        print()
        print("No files were modified. Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
