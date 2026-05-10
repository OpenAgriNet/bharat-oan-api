"""Training-data export adapter.

Converts agent trace records (e.g. fetched from Langfuse) into chat-template
JSONL formats consumable by Hugging Face TRL trainers (`SFTTrainer`,
`DPOTrainer`).

The converter is intentionally decoupled from any specific tracing backend:
callers pass plain dicts that follow the documented `TraceRecord` shape, so the
same code path works for Langfuse exports, replayed unit-test fixtures, or
manually-curated demonstration data.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator, Literal, Optional

try:  # repo-wide preference; falls back to stdlib so the helper stays
    import simplejson as json  # importable in lightweight test environments.
except ImportError:  # pragma: no cover
    import json  # type: ignore[no-redef]

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Output schema (chat-template, TRL-compatible)
# ---------------------------------------------------------------------------


class TrainingMessage(BaseModel):
    """A single message in a chat-template training example.

    Aligned with the OpenAI / Hugging Face chat-template convention so the
    output JSONL drops directly into `SFTTrainer(dataset_text_field=None,
    formatting_func=tokenizer.apply_chat_template)`.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = Field(
        default=None,
        description="Tool name, when role == 'tool' or when an assistant turn "
        "issued a named tool call.",
    )
    tool_call_id: Optional[str] = Field(
        default=None,
        description="Correlates a tool result with the assistant tool call "
        "that triggered it.",
    )


class SFTRecord(BaseModel):
    """One supervised fine-tuning example."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trace_id: str
    messages: list[TrainingMessage]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DPOPair(BaseModel):
    """One Direct Preference Optimization pair.

    Matches `trl.DPOTrainer`'s expected shape: `prompt` is the shared prefix
    rendered as a chat-template string, `chosen` and `rejected` are the
    competing completions.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trace_id: str
    prompt: list[TrainingMessage]
    chosen: TrainingMessage
    rejected: TrainingMessage
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Input schema (lightly typed view over a Langfuse-style trace dict)
# ---------------------------------------------------------------------------


# A trace dict is expected to look roughly like:
#
#     {
#       "id": "trace-...",
#       "input": {"query": "...", "lang_code": "hi", "session_id": "..."},
#       "output": "...final assistant answer...",
#       "observations": [
#         {"type": "generation", "name": "agent.moderation",
#          "input": {...}, "output": "..."},
#         {"type": "tool",       "name": "tool:weather",
#          "input": {"city": "Mumbai"}, "output": {...}, "id": "call-1"},
#         ...
#       ],
#       "metadata": {"environment": "prod", ...}
#     }
#
# We deliberately don't model this with Pydantic — Langfuse's payload shape is
# wider than what we consume, and we want forward-compatibility with extra
# fields. The accessors below tolerate missing keys.


def _as_text(value: Any) -> str:
    """Best-effort serialization of a trace input/output field to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _system_prompt_from_trace(trace: dict[str, Any]) -> Optional[str]:
    """Extract a system prompt if present in trace metadata.

    The bharat-oan-api's `agrinet_agent` builds language-specific instructions
    via `get_prompt(...)`; if a caller has captured that as
    `metadata["system_prompt"]` we surface it as the first message.
    """
    meta = trace.get("metadata") or {}
    sp = meta.get("system_prompt")
    return sp if isinstance(sp, str) and sp.strip() else None


def _user_query_from_trace(trace: dict[str, Any]) -> str:
    """Extract the user query that started the trace."""
    inp = trace.get("input")
    if isinstance(inp, dict):
        for key in ("query", "user_query", "message", "prompt"):
            if key in inp and isinstance(inp[key], str):
                return inp[key]
        return _as_text(inp)
    return _as_text(inp)


def _final_assistant_text(trace: dict[str, Any]) -> str:
    """Extract the final assistant output for the trace."""
    out = trace.get("output")
    return _as_text(out)


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------


def langfuse_trace_to_sft(
    trace: dict[str, Any],
    *,
    include_tool_calls: bool = True,
    drop_empty: bool = True,
) -> Optional[SFTRecord]:
    """Convert one trace dict into an `SFTRecord`.

    Args:
        trace: Trace payload (e.g. one item from `langfuse.fetch_traces()`).
        include_tool_calls: When True, intermediate tool observations are
            preserved as `assistant` (tool-call) and `tool` (tool-result)
            messages. When False, only the final user→assistant turn is
            emitted — useful when training a smaller model that should answer
            directly without learning the tool-use trajectory.
        drop_empty: Skip the trace if either the user query or final
            assistant output is empty.

    Returns:
        An `SFTRecord` ready for JSONL serialization, or `None` if the trace
        was dropped (e.g. empty content with `drop_empty=True`).
    """
    trace_id = str(trace.get("id") or trace.get("trace_id") or "")
    user_text = _user_query_from_trace(trace)
    final_text = _final_assistant_text(trace)

    if drop_empty and (not user_text.strip() or not final_text.strip()):
        logger.debug("Dropping empty trace %s", trace_id or "<no-id>")
        return None

    messages: list[TrainingMessage] = []

    system_prompt = _system_prompt_from_trace(trace)
    if system_prompt:
        messages.append(TrainingMessage(role="system", content=system_prompt))

    messages.append(TrainingMessage(role="user", content=user_text))

    if include_tool_calls:
        for obs in trace.get("observations") or []:
            obs_type = obs.get("type")
            obs_name = obs.get("name") or ""
            if obs_type == "tool" or obs_name.startswith("tool:"):
                tool_name = obs_name.split(":", 1)[-1] if ":" in obs_name else obs_name
                tool_call_id = str(obs.get("id") or "") or None

                # Assistant emits the tool call …
                messages.append(
                    TrainingMessage(
                        role="assistant",
                        content=_as_text(obs.get("input")),
                        name=tool_name or None,
                        tool_call_id=tool_call_id,
                    )
                )
                # … the tool returns its result.
                messages.append(
                    TrainingMessage(
                        role="tool",
                        content=_as_text(obs.get("output")),
                        name=tool_name or None,
                        tool_call_id=tool_call_id,
                    )
                )

    messages.append(TrainingMessage(role="assistant", content=final_text))

    metadata: dict[str, Any] = {}
    src_meta = trace.get("metadata") or {}
    for key in ("environment", "source_lang", "target_lang", "session_id"):
        if key in src_meta:
            metadata[key] = src_meta[key]

    return SFTRecord(trace_id=trace_id, messages=messages, metadata=metadata)


def build_dpo_pair(
    trace: dict[str, Any],
    *,
    chosen_output: str,
    rejected_output: str,
    metadata: Optional[dict[str, Any]] = None,
) -> DPOPair:
    """Build a `DPOPair` from a trace plus an externally-sourced preference.

    The preference signal (which output was preferred) typically comes from a
    human reviewer or a reward model — this helper stays agnostic about how
    that label was produced and only handles the formatting.
    """
    trace_id = str(trace.get("id") or trace.get("trace_id") or "")
    user_text = _user_query_from_trace(trace)

    prompt: list[TrainingMessage] = []
    system_prompt = _system_prompt_from_trace(trace)
    if system_prompt:
        prompt.append(TrainingMessage(role="system", content=system_prompt))
    prompt.append(TrainingMessage(role="user", content=user_text))

    return DPOPair(
        trace_id=trace_id,
        prompt=prompt,
        chosen=TrainingMessage(role="assistant", content=chosen_output),
        rejected=TrainingMessage(role="assistant", content=rejected_output),
        metadata=dict(metadata) if metadata else {},
    )


# ---------------------------------------------------------------------------
# JSONL serialization
# ---------------------------------------------------------------------------


def to_jsonl_line(record: BaseModel) -> str:
    """Serialize one Pydantic record as a single JSONL line (no trailing \\n)."""
    return json.dumps(
        record.model_dump(exclude_none=True), ensure_ascii=False, sort_keys=False
    )


def stream_jsonl(records: Iterable[BaseModel]) -> Iterator[str]:
    """Yield JSONL lines (each terminated by `\\n`) for a stream of records.

    Suitable for `fastapi.responses.StreamingResponse`.
    """
    for record in records:
        yield to_jsonl_line(record) + "\n"
