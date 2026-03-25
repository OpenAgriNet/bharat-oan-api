# app/tasks/tool_tracker.py

from dataclasses import dataclass, field
from typing import Optional
import time

from pydantic_ai import TextPartDelta
from pydantic_ai.messages import ThinkingPart, RetryPromptPart

from helpers.utils import get_logger

logger = get_logger(__name__)


@dataclass
class ToolCall:
    tool_name: str
    tool_call_id: str
    args: Optional[dict]
    started_at: float = field(default_factory=time.monotonic)
    status: str = "pending"       # pending | success | error
    result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None

    def resolve(self, content, is_error: bool):
        self.duration_ms = round((time.monotonic() - self.started_at) * 1000, 1)
        if is_error:
            self.status = "error"
            self.error = str(content) if content is not None else None
            self.result = None
        else:
            self.status = "success"
            self.result = str(content)
            self.error = None

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "args":      self.args,
            "status":    self.status,
            "result":    self.result,
            "error":     self.error,
            "duration_ms": self.duration_ms,
        }


class ToolUsageTracker:
    """
    Collects tool calls/results during a single agent stream.
    Keyed by tool_call_id for O(1) resolution.

    Also acts as the central event dispatcher for `run_stream_events`,
    so callers only need a thin loop instead of a large if/elif chain.
    """

    def __init__(self):
        self._calls: dict[str, ToolCall] = {}
        self._order: list[str] = []          # preserve call sequence
        self._final_result_found: bool = False
        self.new_messages = None             # set on agent_run_result
        self.result_obj = None               # set on agent_run_result

    # ── Low-level hooks (unchanged) ─────────────────────────────────

    def on_call(self, tool_name: str, tool_call_id: str, args=None):
        """Call when function_tool_call event fires."""
        if tool_name in ("final_result", "json"):   
            return
        tc = ToolCall(tool_name=tool_name, tool_call_id=tool_call_id, args=args)
        self._calls[tool_call_id] = tc
        self._order.append(tool_call_id)

    def on_result(self, tool_call_id: str, content, is_error: bool):
        """Call when function_tool_result event fires."""
        tc = self._calls.get(tool_call_id)
        if tc:
            tc.resolve(content, is_error)

    # ── High-level event dispatcher ─────────────────────────────────

    def process_event(self, event) -> Optional[str]:
        """
        Process a single ``run_stream_events`` event.

        Returns a text delta string when one should be yielded to the
        caller, otherwise returns ``None``.
        """
        kind = getattr(event, "event_kind", "")

        if kind == "part_start":
            if isinstance(event.part, ThinkingPart):
                logger.info("Reasoning part started (not streamed to user)")

        elif kind == "part_delta":
            if isinstance(event.delta, TextPartDelta):
                if self._final_result_found and event.delta.content_delta:
                    return event.delta.content_delta

        elif kind == "final_result":
            logger.info("[Result] The model started producing a final result")
            self._final_result_found = True

        elif kind == "function_tool_call":
            tool_name = event.part.tool_name
            tool_call_id = getattr(event.part, "tool_call_id", None)
            args = getattr(event.part, "args", None)
            logger.info(f"Tool call: {tool_name} (id={tool_call_id})")
            self.on_call(tool_name, tool_call_id, args)

        elif kind == "function_tool_result":
            result_part = getattr(event, "result", None)
            tool_call_id = getattr(result_part, "tool_call_id", None)
            tool_name_result = getattr(result_part, "tool_name", "unknown")
            content = getattr(result_part, "content", None)
            is_error = isinstance(result_part, RetryPromptPart)
            logger.info(
                f"Tool result: {tool_name_result} (id={tool_call_id}) "
                f"status={'error' if is_error else 'success'}"
            )
            self.on_result(tool_call_id, content, is_error)
            # Reset — model may produce another turn after a tool result
            self._final_result_found = False

        elif kind == "agent_run_result":
            self.result_obj = event.result
            self.new_messages = event.result.new_messages()

        return None

    # ── Serialisation / queries ─────────────────────────────────────

    def as_list(self) -> list[dict]:
        return [self._calls[tid].to_dict() for tid in self._order if tid in self._calls]

    @property
    def has_errors(self) -> bool:
        return any(tc.status == "error" for tc in self._calls.values())

    @property
    def tool_names(self) -> list[str]:
        return [self._calls[tid].tool_name for tid in self._order if tid in self._calls]