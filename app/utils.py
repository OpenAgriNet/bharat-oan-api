import json
from typing import List, Optional
from app.core.cache import cache  # Import cache instance from core
from helpers.utils import get_logger
from copy import deepcopy
from helpers.ffs_model_view import compact_ffs_result
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelMessage,
)
from pydantic_core import to_jsonable_python

HISTORY_SUFFIX = "_SVA"

DEFAULT_CACHE_TTL = 60*60*2 # 2 hours

logger = get_logger(__name__)

# Cache utility functions
async def get_cache(key: str):
    """
    Get value from cache.
    
    Args:
        key: Cache key to retrieve
        
    Returns:
        Cached value or None if not found
    """
    return await cache.get(key)


async def set_cache(key: str, value, ttl: int = DEFAULT_CACHE_TTL):
    """
    Set value in cache with TTL.
    
    Args:
        key: Cache key to store under
        value: Value to cache (will be JSON serialized)
        ttl: Time to live in seconds (default: 2 hours)
        
    Returns:
        True if successful
    """
    await cache.set(key, value, ttl=ttl)
    return True


async def _get_message_history(session_id: str) -> List[ModelMessage]:
    """Get or initialize message history."""
    message_history = await get_cache(f"{session_id}_{HISTORY_SUFFIX}")
    if message_history:
        return clean_chat_history(ModelMessagesTypeAdapter.validate_python(message_history))
    return []

async def _get_moderation_history(session_id: str) -> List[ModelMessage]:
    """Get or initialize moderation history."""
    moderation_history = await get_cache(f"{session_id}_{HISTORY_SUFFIX}_MODERATION")
    if moderation_history:
        return ModelMessagesTypeAdapter.validate_python(moderation_history)
    return []

async def update_message_history(session_id: str, all_messages: List[ModelMessage]):
    """Update message history."""
    await set_cache(f"{session_id}_{HISTORY_SUFFIX}", to_jsonable_python(clean_chat_history(all_messages)), ttl=DEFAULT_CACHE_TTL)


def _strip_saved_chat_context(text: str) -> str:
    """Remove our legacy conversation wrapper and generated one-line FFS/session-state snapshots."""
    if text.startswith("**Conversation**\n\n"):
        _, separator, current = text.partition("\n\n---\n\n")
        if separator:
            text = current
    # The snapshot is appended after our language metadata, not inside user text.
    before, language, after = text.rpartition("\n**Selected Language:** ")
    if language:
        after = "".join(line for line in after.splitlines(keepends=True)
                        if not line.startswith(("**FFS workflow:** ", "**Session state")))
        text = before + language + after
    return text


def clean_chat_history(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Keep conversation/tool evidence, without saved copies of injected context.

    Apply on load for existing sessions and on save for new turns. Only user-prompt
    strings change; tool parts, images and assistant output remain intact.
    """
    cleaned = []
    for message in messages:
        copy = message
        for index, part in enumerate(message.parts):
            if getattr(part, "part_kind", "") == "tool-return" and getattr(part, "tool_name", "") in {"ffs_find_shops", "ffs_create_booking"}:
                compact = compact_ffs_result(part.content)
                if compact != part.content:
                    if copy is message:
                        copy = deepcopy(message)
                    copy.parts[index].content = compact
                continue
            if getattr(part, "part_kind", "") != "user-prompt":
                continue
            content = part.content
            if isinstance(content, str):
                stripped = _strip_saved_chat_context(content)
            elif isinstance(content, list):
                stripped = [_strip_saved_chat_context(item) if isinstance(item, str) else item for item in content]
            else:
                continue
            if stripped != content:
                if copy is message:
                    copy = deepcopy(message)
                copy.parts[index].content = stripped
        cleaned.append(copy)
    return cleaned

async def update_moderation_history(session_id: str, moderation_messages: List[ModelMessage]):
    """Update moderation history."""
    await set_cache(f"{session_id}_{HISTORY_SUFFIX}_MODERATION", to_jsonable_python(moderation_messages), ttl=DEFAULT_CACHE_TTL)

def filter_out_tool_calls(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Filter out tool calls and tool returns from the message history.

    Args:
        messages: List of messages (ModelRequest/ModelResponse objects)

    Returns:
        List of messages with tool calls and returns removed
    """
    if not messages:
        return []

    filtered_messages = []
    for message in messages:
        # Create a deep copy to avoid modifying the original
        msg_copy = deepcopy(message)
        filtered_parts = []

        for part in msg_copy.parts:
            # Only keep non-tool parts
            if not hasattr(part, 'part_kind') or part.part_kind not in ['tool-call', 'tool-return']:
                filtered_parts.append(part)

        # Only add messages that have non-tool parts
        if filtered_parts:
            msg_copy.parts = filtered_parts
            filtered_messages.append(msg_copy)
    return filtered_messages


def filter_thinking_from_history(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Remove ThinkingPart from ModelResponse messages in history.

    vLLM/OpenAI-compatible servers choke when pydantic-ai wraps ThinkingPart
    content back into <think> tags in the assistant message (causes
    "Unknown role: final" errors).  Stripping them from history also prevents
    leaked <think> content from accumulating across multi-turn conversations.

    Args:
        messages: List of ModelMessage (ModelRequest / ModelResponse).

    Returns:
        New list with ThinkingPart removed from every ModelResponse.
    """
    if not messages:
        return messages

    cleaned: List[ModelMessage] = []
    for message in messages:
        if getattr(message, 'kind', '') == 'response':
            non_thinking = [p for p in message.parts if getattr(p, 'part_kind', '') != 'thinking']
            if non_thinking:
                msg_copy = deepcopy(message)
                msg_copy.parts = non_thinking
                cleaned.append(msg_copy)
            # Drop the message entirely if it had only thinking parts
        else:
            cleaned.append(message)
    return cleaned



def get_message_pairs(history: List[ModelMessage], limit: int = None) -> List[List]:
    """Extract user/assistant message part pairs from history, starting with the most recent.
    
    Args:
        history: List of messages (ModelMessage objects)
        limit: Maximum number of message pairs to return (None = all pairs)
        
    Returns:
        List of [UserPromptPart, TextPart] pairs, starting with the most recent
    """
    if not history:
        return []
    
    pairs = []
    # Process messages in reverse chronological order (newest first)
    i = len(history) - 1
    
    while i > 0 and (limit is None or len(pairs) < limit):
        # Find the nearest assistant message (with 'text' part)
        assistant_idx = None
        text_part = None
        for j in range(i, -1, -1):
            # Find the TextPart in the message
            for part in history[j].parts:
                if getattr(part, "part_kind", "") == "text":
                    assistant_idx = j
                    text_part = part
                    break
            if assistant_idx is not None:
                break
        
        if assistant_idx is None or text_part is None:
            break  # No more assistant messages
            
        # Find the nearest user message before the assistant message
        user_idx = None
        user_part = None
        for j in range(assistant_idx - 1, -1, -1):
            # Find the UserPromptPart in the message
            for part in history[j].parts:
                if getattr(part, "part_kind", "") == "user-prompt":
                    user_idx = j
                    user_part = part
                    break
            if user_idx is not None:
                break
                
        if user_idx is None or user_part is None:
            break  # No more user messages
            
        # Add the pair and continue searching from before this pair
        pairs.append([deepcopy(user_part), deepcopy(text_part)])
        i = user_idx - 1
        
    return pairs

def format_message_pairs(history: List[ModelMessage], limit: int = None) -> List[str]:
    """Format user/assistant message pairs as strings with custom headers.
    
    Args:
        history: List of messages (ModelMessage objects)
        limit: Maximum number of message pairs to return (None = all pairs)
        
    Returns:
        List of formatted strings containing user and assistant messages
    """
    pairs = get_message_pairs(history, limit)
    pairs = pairs[::-1]  # get_message_pairs returns newest-first; reverse to chronological so the most recent pair sits adjacent to the current user message
    formatted_messages = []

    for user_part, assistant_part in pairs:
        # Strip the injected "**Conversation**\n\n...\n\n---\n\n" prefix from prior turns
        # so message pairs don't recursively embed previous conversation blocks.
        user_content = user_part.content
        separator = "\n\n---\n\n"
        if separator in user_content:
            user_content = user_content.split(separator, 1)[1]
        formatted_pair = f"""**User Message**:\n{user_content}\n\n**Assistant Message**:\n{assistant_part.content}"""
        formatted_messages.append(formatted_pair)
    
    return formatted_messages


### Second method to trim history

def group_convos(history: List[ModelMessage]):
    convos = []
    current = []

    for msg in history:
        has_user = any(getattr(p, "part_kind", "") == "user-prompt" for p in msg.parts)

        if has_user and current:
            # close previous convo
            convos.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        convos.append(current)

    return convos

def convo_token_usage(convo: list[ModelMessage]) -> int:
    # Size of the turn's own messages. A response's usage.total_tokens also counts the whole system
    # prompt and tool definitions (~64k tokens here), which made every turn exceed the history budget
    # and dropped all history after the first turn. ~4 characters per token is close enough for a budget.
    return len(json.dumps(to_jsonable_python(convo), ensure_ascii=False)) // 4

def trim_history(
    history: List[ModelMessage],
    max_tokens: int = 28_000,
    # include_system_prompts=True,
    # include_tool_calls=True,
) -> List[ModelMessage]:
    if not history:
        return []

    convos = group_convos(history)
    if not convos:
        return []

    # Build list of (messages, tokens)
    convo_infos = []
    for convo in convos:
        tokens = convo_token_usage(convo)
        convo_infos.append({"messages": convo, "tokens": tokens})

    # Always keep convo 0 (system + first interaction)
    first = convo_infos[0]
    rest = convo_infos[1:]

    total_tokens = first["tokens"]
    selected = []

    # Walk from newest convo backwards
    for info in reversed(rest):
        if total_tokens + info["tokens"] <= max_tokens:
            selected.insert(0, info)  # maintain chronological order
            total_tokens += info["tokens"]
        else:
            break

    final_convos = [first] + selected

    trimmed: List[ModelMessage] = []
    for info in final_convos:
        trimmed.extend(info["messages"])

    logger.info(f"Trimmed history: {total_tokens} tokens (max: {max_tokens})")

    return trimmed


TOOL_PART_KINDS = ("tool-call", "tool-return", "retry-prompt")


def split_turns(history: List[ModelMessage]) -> List[List[ModelMessage]]:
    """One list per farmer turn: the user prompt, the agent's tool calls/results and its answer."""
    return group_convos(history)


def last_user_message(history: List[ModelMessage]) -> Optional[str]:
    for message in reversed(history or []):
        for part in reversed(message.parts):
            if getattr(part, "part_kind", "") == "user-prompt":
                return part.content if isinstance(part.content, str) else str(part.content)
    return None


OLD_TOOL_RESULT_STUB = "[Earlier result not repeated. Facts needed later are in the session state.]"


def _stub_tool_results(message: ModelMessage) -> ModelMessage:
    if not any(getattr(part, "part_kind", "") == "tool-return" for part in message.parts):
        return message
    copy = deepcopy(message)
    for part in copy.parts:
        if getattr(part, "part_kind", "") == "tool-return":
            part.content = OLD_TOOL_RESULT_STUB
    return copy


def _drop_tool_parts(message: ModelMessage) -> Optional[ModelMessage]:
    parts = [part for part in message.parts if getattr(part, "part_kind", "") not in TOOL_PART_KINDS]
    if len(parts) == len(message.parts):
        return message
    if not parts:
        return None
    copy = deepcopy(message)
    copy.parts = parts
    return copy


def keep_tool_results(history: List[ModelMessage], last_n_turns: int, stub: bool = False) -> List[ModelMessage]:
    """Keep tool results only in the last n turns; earlier turns keep their prompt and answer text.

    stub=False drops older tool calls and results together. stub=True keeps older tool calls (small) and
    replaces each result with a short placeholder, so the history still shows that earlier answers came from
    tools; without that, the model tends to repeat earlier tool-backed wording ("OTP sent") without calling the
    tool. Either way every call keeps a matching return, so the history stays valid for the model API.
    """
    turns = split_turns(history)
    cut = max(len(turns) - last_n_turns, 0)
    kept: List[ModelMessage] = []
    transform = _stub_tool_results if stub else _drop_tool_parts
    for index, turn in enumerate(turns):
        if index >= cut:
            kept.extend(turn)
            continue
        for message in turn:
            transformed = transform(message)
            if transformed is not None:
                kept.append(transformed)
    return kept


def estimate_tokens(value) -> int:
    """Gemma-token estimate that does not undercount Indic scripts.

    Measured on chat text: English ~3.8 characters per token, Hindi ~3.4, Punjabi ~2.2. Counting ASCII as 4
    characters and everything else as 2 characters per token stays at or above the real count.
    """
    text = value if isinstance(value, str) else json.dumps(to_jsonable_python(value), ensure_ascii=False)
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars // 4 + (len(text) - ascii_chars) // 2


def _turn_tokens(turn: List[ModelMessage]) -> int:
    return sum(estimate_tokens(getattr(p, "args", None) if getattr(p, "part_kind", "") == "tool-call"
                               else getattr(p, "content", ""))
               for message in turn for p in message.parts
               if getattr(p, "part_kind", "") not in ("system-prompt", "thinking"))


def recent_turns(history: List[ModelMessage], max_tokens: int, min_turns: int) -> List[ModelMessage]:
    """Newest turns within max_tokens of conversation, but never fewer than min_turns.

    Only conversation counts, not the stored system prompt. The first message's system-prompt part is always
    kept (alone, if its turn is dropped) because the agent replaces it with the current dynamic prompt.
    """
    turns = split_turns(history)
    kept: List[List[ModelMessage]] = []
    total = 0
    for turn in reversed(turns):
        tokens = _turn_tokens(turn)
        if len(kept) >= min_turns and total + tokens > max_tokens:
            break
        kept.insert(0, turn)
        total += tokens
    messages = [m for turn in kept for m in turn]
    if turns and kept and kept[0] is not turns[0]:
        system = [p for p in turns[0][0].parts if getattr(p, "part_kind", "") == "system-prompt"]
        if system:
            first = deepcopy(turns[0][0])
            first.parts = system
            messages.insert(0, first)
    return messages


def build_model_history(
    history: List[ModelMessage],
    *,
    tool_turns: Optional[int] = None,
    max_tokens: int = 64_000,
    conversation_tokens: Optional[int] = None,
    min_turns: int = 3,
    stub_old_results: bool = False,
) -> List[ModelMessage]:
    """History sent to the model.

    tool_turns=None resends every kept turn's tool results (bh-dev behaviour). A number keeps tool results only
    for that many latest turns; what later turns need from older tools comes from session state instead.
    conversation_tokens set: keep the newest turns within that many conversation tokens (at least min_turns);
    otherwise bh-dev's trim to max_tokens.
    """
    if tool_turns is not None:
        history = keep_tool_results(history, tool_turns, stub=stub_old_results)
    if conversation_tokens is not None:
        history = recent_turns(history, conversation_tokens, min_turns)
    else:
        history = trim_history(history, max_tokens=max_tokens)
    return filter_thinking_from_history(history)
