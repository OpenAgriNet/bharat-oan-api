from typing import List
from app.core.cache import cache  # Import cache instance from core
from helpers.utils import get_logger
from copy import deepcopy
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
        return ModelMessagesTypeAdapter.validate_python(message_history)
    return []

async def _get_moderation_history(session_id: str) -> List[ModelMessage]:
    """Get or initialize moderation history."""
    moderation_history = await get_cache(f"{session_id}_{HISTORY_SUFFIX}_MODERATION")
    if moderation_history:
        return ModelMessagesTypeAdapter.validate_python(moderation_history)
    return []

async def update_message_history(session_id: str, all_messages: List[ModelMessage]):
    """Update message history."""
    await set_cache(f"{session_id}_{HISTORY_SUFFIX}", to_jsonable_python(all_messages), ttl=DEFAULT_CACHE_TTL)

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
    formatted_messages = []
    
    for user_part, assistant_part in pairs:
        formatted_pair = f"""**User Message**:\n{user_part.content}\n\n**Assistant Message**:\n{assistant_part.content}"""
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
    tokens = 0
    for msg in convo:
        if getattr(msg, "kind", "") == "response" and getattr(msg, "usage", None):
            tokens += msg.usage.total_tokens
    return tokens

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


def extract_final_text(messages: List[ModelMessage]) -> str:
    """Return the last TextPart content from a message list."""
    for msg in reversed(messages):
        for part in getattr(msg, "parts", []):
            if getattr(part, "part_kind", "") == "text":
                return part.content
    return ""