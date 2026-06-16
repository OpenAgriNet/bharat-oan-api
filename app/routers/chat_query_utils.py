import re
import uuid
from typing import List, Optional, Tuple

from fastapi import UploadFile

from app.core.image_storage import save_uploaded_image
from app.utils import _get_message_history


DEFAULT_IMAGE_ANALYSIS_PROMPT = "I uploaded a crop image."

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)

EMBEDDED_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)

IMAGE_URL_ID_PATTERN = re.compile(r"/api/image/([0-9a-fA-F\-]{36})")
TAGGED_IMAGE_ID_PATTERN = re.compile(r"\[IMAGE_ID:\s*([0-9a-fA-F\-]{36})\]")


def _clean_image_query(query_text: Optional[str]) -> str:
    prompt = (query_text or "").strip()
    if not prompt:
        return DEFAULT_IMAGE_ANALYSIS_PROMPT

    if "/api/image/" in prompt:
        prompt = IMAGE_URL_ID_PATTERN.sub("", prompt)
    prompt = TAGGED_IMAGE_ID_PATTERN.sub("", prompt)
    prompt = EMBEDDED_UUID_PATTERN.sub("", prompt)
    prompt = " ".join(prompt.split())
    return prompt or DEFAULT_IMAGE_ANALYSIS_PROMPT


def extract_image_id(query_text: str) -> Optional[str]:
    query = (query_text or "").strip()
    if not query:
        return None

    if UUID_PATTERN.fullmatch(query):
        return query

    url_match = IMAGE_URL_ID_PATTERN.search(query)
    if url_match:
        return url_match.group(1)

    tagged_match = TAGGED_IMAGE_ID_PATTERN.search(query)
    if tagged_match:
        return tagged_match.group(1)

    embedded_match = EMBEDDED_UUID_PATTERN.search(query)
    if embedded_match:
        return embedded_match.group(0)

    return None


def build_image_analysis_message(query: Optional[str] = None) -> str:
    prompt = _clean_image_query(query)
    return f"{prompt}\n\n[IMAGE_UPLOADED]"


def normalize_chat_query(query_text: str) -> Tuple[str, bool]:
    image_id = extract_image_id(query_text)
    has_image_url = "/api/image/" in (query_text or "")
    is_image_analysis = bool(image_id or has_image_url)

    if image_id or has_image_url:
        return build_image_analysis_message(query_text), True

    return query_text, is_image_analysis


def ensure_session_id(session_id: Optional[str]) -> str:
    return session_id or str(uuid.uuid4())


async def prepare_image_analyze_payload(
    image: UploadFile,
    query: Optional[str],
    session_id: Optional[str],
) -> Tuple[str, str, List]:
    resolved_session_id, history = await get_session_history(session_id)
    await save_uploaded_image(image)
    user_message = build_image_analysis_message(query)
    return resolved_session_id, user_message, history


async def get_session_history(session_id: Optional[str]) -> Tuple[str, List]:
    resolved_session_id = ensure_session_id(session_id)
    history = await _get_message_history(resolved_session_id)
    return resolved_session_id, history
