"""Null-safe Langfuse helpers for voice calls; tracing failures never fail a call."""
from contextlib import ExitStack, contextmanager, nullcontext
import json
from typing import Any, Dict, Generator, Optional

from helpers import langfuse_helper  # noqa: F401  configures the chat and voice Langfuse projects
from helpers.utils import get_logger

logger = get_logger(__name__)

def get_langfuse():
    """The Langfuse client for the current request's project (voice on the voice endpoint).

    Not cached: helpers.langfuse_helper.LangfuseProjectMiddleware selects the project per
    request, and a client cached outside a request would be Langfuse's disabled client.
    """
    try:
        from langfuse import get_client  # type: ignore
    except ImportError as e:  # pragma: no cover
        logger.warning("Langfuse SDK not available, skipping (err=%r)", e)
        return None
    try:
        return get_client()
    except Exception as e:
        logger.exception("Failed to init Langfuse client: %r", e)
        return None


def _compact_kwargs(**kwargs: Any) -> Dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


@contextmanager
def safe_propagate_attributes(
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    version: Optional[str] = None,
) -> Generator[None, None, None]:
    # Only the Langfuse setup is guarded — the yield must stay outside the
    # try, otherwise exceptions raised by the caller's block get thrown into
    # this generator, swallowed, and replaced by "generator didn't stop after
    # throw()", hiding the real error.
    with ExitStack() as stack:
        if get_langfuse() is not None:
            try:
                from langfuse import propagate_attributes  # type: ignore

                stack.enter_context(
                    propagate_attributes(
                        **_compact_kwargs(
                            user_id=user_id,
                            session_id=session_id,
                            tags=tags,
                            metadata=metadata,
                            version=version,
                        )
                    )
                )
            except Exception as e:
                logger.warning("Langfuse propagate_attributes skipped (err=%r)", e)
        yield


def safe_start_observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any = None,
    output: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
):
    # Note: Langfuse observations do not support tags — tags are trace-level only.
    # Use safe_propagate_attributes(tags=...) instead.
    client = get_langfuse()
    if client is None:
        return nullcontext(None)
    try:
        return client.start_as_current_observation(
            **_compact_kwargs(
                as_type=as_type,
                name=name,
                input=input,
                output=output,
                metadata=metadata,
                model=model,
            )
        )
    except Exception:
        return nullcontext(None)


def safe_flush() -> None:
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass


def safe_start_agent_observation(
    *,
    name: str,
    input: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Langfuse ``agent`` observation; children include PydanticAI OTEL spans (invoke_agent, execute_tool, …).

    Tags are trace-level only in Langfuse; use safe_propagate_attributes(tags=...) for those.
    """
    client = get_langfuse()
    if client is None:
        return nullcontext(None)
    try:
        return client.start_as_current_observation(
            **_compact_kwargs(
                as_type="agent",
                name=name,
                input=input,
                metadata=metadata,
            )
        )
    except Exception:
        return nullcontext(None)


def safe_update_current_span(
    *,
    metadata: Optional[Dict[str, Any]] = None,
    input: Any = None,
    output: Any = None,
    level: Optional[str] = None,
) -> None:
    """Attach metadata (and optionally input/output) to the active observation (e.g. inside an ``@observe`` tool)."""
    client = get_langfuse()
    if client is None:
        return
    fields = {"metadata": metadata, "input": input, "output": output, "level": level}
    try:
        client.update_current_span(**{k: v for k, v in fields.items() if v is not None})
    except Exception:
        pass


def voice_output_summary(last_chunk: str) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "audio": "",
        "end_interaction": False,
    }
    if not last_chunk:
        return summary
    try:
        parsed = json.loads(last_chunk)
        if isinstance(parsed, dict):
            summary["audio"] = parsed.get("audio") or ""
            summary["end_interaction"] = bool(parsed.get("end_interaction", False))
    except (json.JSONDecodeError, TypeError, ValueError):
        return summary
    return summary


def safe_update_observation(observation: Any, output: Dict[str, Any]) -> None:
    if observation is None or not hasattr(observation, "update"):
        return
    try:
        observation.update(output=output)
    except (TypeError, AttributeError):
        return
