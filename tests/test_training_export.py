"""Unit tests for the training-data export adapter.

These tests deliberately avoid hitting Langfuse — they assert the conversion
contract against in-memory trace fixtures shaped like the dicts Langfuse
returns. That keeps the suite hermetic and lets reviewers run it without any
secrets or network access.
"""

from __future__ import annotations

import json

from helpers.training_export import (
    SCHEMA_VERSION,
    DPOPair,
    SFTRecord,
    build_dpo_pair,
    langfuse_trace_to_sft,
    stream_jsonl,
    to_jsonl_line,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _trace_simple() -> dict:
    """A minimal one-turn trace with no tool calls."""
    return {
        "id": "trace-simple-1",
        "input": {
            "query": "When should I sow wheat in Punjab?",
            "lang_code": "en",
            "session_id": "sess-123",
        },
        "output": "Sow wheat between mid-October and mid-November in Punjab.",
        "metadata": {
            "environment": "test",
            "source_lang": "en",
            "target_lang": "en",
            "session_id": "sess-123",
        },
        "observations": [],
    }


def _trace_with_tool_call() -> dict:
    """A trace where the agent calls a weather tool before answering."""
    return {
        "id": "trace-tool-1",
        "input": {"query": "Will it rain in Mumbai tomorrow?"},
        "output": "Yes, light rain is expected in Mumbai tomorrow.",
        "metadata": {"environment": "test", "system_prompt": "You are Vistaar."},
        "observations": [
            {
                "type": "tool",
                "name": "tool:weather",
                "id": "call-1",
                "input": {"city": "Mumbai", "when": "tomorrow"},
                "output": {"forecast": "light rain", "confidence": 0.82},
            }
        ],
    }


# ---------------------------------------------------------------------------
# SFT conversion
# ---------------------------------------------------------------------------


def test_simple_trace_round_trip():
    record = langfuse_trace_to_sft(_trace_simple())
    assert record is not None
    assert record.schema_version == SCHEMA_VERSION
    assert record.trace_id == "trace-simple-1"

    roles = [m.role for m in record.messages]
    assert roles == ["user", "assistant"]
    assert record.messages[0].content.startswith("When should I sow wheat")
    assert "mid-October" in record.messages[1].content

    # Selected metadata is propagated; other keys are not.
    assert record.metadata["environment"] == "test"
    assert "system_prompt" not in record.metadata


def test_system_prompt_is_first_message_when_present():
    record = langfuse_trace_to_sft(_trace_with_tool_call())
    assert record is not None
    assert record.messages[0].role == "system"
    assert record.messages[0].content == "You are Vistaar."
    # Followed by user → assistant(tool-call) → tool → assistant(final)
    assert [m.role for m in record.messages[1:]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_tool_calls_can_be_dropped():
    record = langfuse_trace_to_sft(
        _trace_with_tool_call(), include_tool_calls=False
    )
    assert record is not None
    # System (from metadata) + user + final assistant only.
    assert [m.role for m in record.messages] == ["system", "user", "assistant"]


def test_tool_call_preserves_correlation_id():
    record = langfuse_trace_to_sft(_trace_with_tool_call())
    assert record is not None
    assistant_call = next(
        m for m in record.messages if m.role == "assistant" and m.tool_call_id
    )
    tool_result = next(m for m in record.messages if m.role == "tool")
    assert assistant_call.tool_call_id == tool_result.tool_call_id == "call-1"
    assert assistant_call.name == tool_result.name == "weather"


def test_empty_trace_is_dropped_by_default():
    empty = {"id": "trace-empty", "input": {"query": ""}, "output": ""}
    assert langfuse_trace_to_sft(empty) is None


def test_empty_trace_can_be_kept_explicitly():
    empty = {"id": "trace-empty", "input": {"query": ""}, "output": ""}
    record = langfuse_trace_to_sft(empty, drop_empty=False)
    assert record is not None
    assert record.trace_id == "trace-empty"


def test_non_dict_input_is_serialized_as_text():
    """A trace whose input is a bare string still produces a valid record."""
    trace = {
        "id": "trace-str",
        "input": "raw string query",
        "output": "answer",
    }
    record = langfuse_trace_to_sft(trace)
    assert record is not None
    user_message = next(m for m in record.messages if m.role == "user")
    assert user_message.content == "raw string query"


# ---------------------------------------------------------------------------
# DPO pair construction
# ---------------------------------------------------------------------------


def test_dpo_pair_uses_external_preference_signal():
    pair = build_dpo_pair(
        _trace_simple(),
        chosen_output="Mid-October to mid-November.",
        rejected_output="Sometime in summer.",
        metadata={"annotator": "human-1"},
    )
    assert isinstance(pair, DPOPair)
    assert pair.trace_id == "trace-simple-1"
    assert pair.chosen.content.startswith("Mid-October")
    assert pair.rejected.content == "Sometime in summer."
    assert pair.metadata == {"annotator": "human-1"}
    # Prompt only carries up to the user turn — never the assistant answer.
    assert [m.role for m in pair.prompt] == ["user"]


# ---------------------------------------------------------------------------
# JSONL serialization
# ---------------------------------------------------------------------------


def test_jsonl_line_is_valid_single_line_json():
    record = langfuse_trace_to_sft(_trace_simple())
    line = to_jsonl_line(record)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["trace_id"] == "trace-simple-1"
    assert parsed["schema_version"] == SCHEMA_VERSION
    assert isinstance(parsed["messages"], list)


def test_stream_jsonl_yields_newline_terminated_lines():
    records = [
        langfuse_trace_to_sft(_trace_simple()),
        langfuse_trace_to_sft(_trace_with_tool_call()),
    ]
    chunks = list(stream_jsonl([r for r in records if r is not None]))
    assert all(chunk.endswith("\n") for chunk in chunks)
    # Each chunk parses independently.
    for chunk in chunks:
        json.loads(chunk)


def test_jsonl_omits_none_fields():
    """tool_call_id / name should not appear on plain user / assistant lines."""
    record = langfuse_trace_to_sft(_trace_simple())
    line = to_jsonl_line(record)
    parsed = json.loads(line)
    user = next(m for m in parsed["messages"] if m["role"] == "user")
    assert "tool_call_id" not in user
    assert "name" not in user
