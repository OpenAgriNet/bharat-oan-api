"""OpenAI-compatible chat-completions envelope around a voice turn (what Sarvam calls)."""
from typing import AsyncGenerator, Dict, Any
import json
import time
import uuid
from app.voice.models import ChatCompletionRequest
from app.voice.languages import iso_language_code
from app.voice.service import stream_voice_message
from app.voice.history import get_voice_history
from app.voice.observability import (
    safe_propagate_attributes,
    safe_start_observation,
    safe_update_observation,
    voice_output_summary,
)

async def generate_openai_stream(
    request: ChatCompletionRequest,
    session_id: str,
    user_id: str,
    language_code: str,
    ffs_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generate OpenAI-compatible SSE streaming response.

    Each chunk contains the latest complete JSON payload in the content delta.
    The client should use the most recent chunk as the current state.
    """
    completion_id = f"chatcmpl-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    created_timestamp = int(time.time())
    query = [msg.content for msg in request.messages if msg.role == "user"][-1]

    existing_history = await get_voice_history(
        session_id, target_lang=language_code, user_id=user_id
    )
    last_chunk = ""
    langfuse_tags = ["bh-voice", "streaming"]

    with safe_start_observation(
        as_type="span",
        name="voice.chat_completions.stream",
        input={"query": query, "target_lang": iso_language_code(language_code)},
    ) as root_obs:
        with safe_propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=langfuse_tags,
        ):
            try:
                with safe_start_observation(
                    as_type="generation",
                    name="voice",
                    model=request.model,
                    input=query
                ) as generation_obs:
                    async for chunk in stream_voice_message(
                        query=query,
                        session_id=session_id,
                        user_id=user_id,
                        history=existing_history,
                        language_code=language_code,
                        ffs_context=ffs_context,
                    ):
                        if chunk:
                            last_chunk = chunk
                            chunk_data = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_timestamp,
                                "model": request.model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": chunk},
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk_data)}\n\n"
                    safe_update_observation(generation_obs, voice_output_summary(last_chunk))
            except Exception:
                safe_update_observation(root_obs, {"audio": "", "end_interaction": False})
                raise
            finally:
                safe_update_observation(root_obs, voice_output_summary(last_chunk))

    finish_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_timestamp,
        "model": request.model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop"
        }]
    }
    yield f"data: {json.dumps(finish_chunk)}\n\n"
    yield "data: [DONE]\n\n"

async def generate_openai_response(
    request: ChatCompletionRequest,
    session_id: str,
    user_id: str,
    language_code: str,
    tenant_id: str | None = None,
    ffs_context: dict | None = None,
) -> Dict[str, Any]:
    """
    Generate OpenAI-compatible non-streaming response.
    Returns the final complete JSON output as content.
    """
    completion_id = f"chatcmpl-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    created_timestamp = int(time.time())
    query = [msg.content for msg in request.messages if msg.role == "user"][-1]

    existing_history = await get_voice_history(
        session_id, target_lang=language_code, user_id=user_id
    )

    last_chunk = ""
    langfuse_tags = ["bh-voice", "non-streaming"]
    if tenant_id:
        langfuse_tags.append(f"tenant:{tenant_id}")

    with safe_start_observation(
        as_type="span",
        name="voice.chat_completions",
        input={"query": query, "target_lang": iso_language_code(language_code)},
    ) as root_obs:
        with safe_propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=langfuse_tags,
        ):
            with safe_start_observation(
                as_type="generation",
                name="voice",
                model=request.model,
                input=query,
            ) as generation_obs:
                async for chunk in stream_voice_message(
                    query=query,
                    session_id=session_id,
                    user_id=user_id,
                    history=existing_history,
                    language_code=language_code,
                    emit_partial=False,
                    ffs_context=ffs_context,
                ):
                    if chunk:
                        last_chunk = chunk
                safe_update_observation(generation_obs, voice_output_summary(last_chunk))
        safe_update_observation(root_obs, voice_output_summary(last_chunk))

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_timestamp,
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": last_chunk
            },
            "finish_reason": "stop"
        }]
    }
