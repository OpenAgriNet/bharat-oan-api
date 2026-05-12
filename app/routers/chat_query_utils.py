import os
import re
import uuid
from typing import List, Optional, Tuple

from fastapi import UploadFile

from app.core.image_storage import save_uploaded_image
from app.utils import _get_message_history


DEFAULT_IMAGE_ANALYSIS_PROMPT = "Analyze this crop image for pests or diseases."

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)

IMAGE_URL_ID_PATTERN = re.compile(r"/api/image/([0-9a-fA-F\-]{36})")
TAGGED_IMAGE_ID_PATTERN = re.compile(r"\[IMAGE_ID:\s*([0-9a-fA-F\-]{36})\]")


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

    return None


def build_internal_image_url(image_id: str) -> str:
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/api/image/{image_id}"


def build_image_analysis_message(image_url: str, query: Optional[str] = None) -> str:
    prompt = (query or "").strip() or DEFAULT_IMAGE_ANALYSIS_PROMPT
    return f"{prompt}\n\n[IMAGE_URL: {image_url}]"


def normalize_chat_query(query_text: str) -> Tuple[str, bool]:
    image_id = extract_image_id(query_text)
    has_image_url = "/api/image/" in (query_text or "")
    is_image_analysis = bool(image_id or has_image_url)

    if image_id:
        image_url = build_internal_image_url(image_id)
        return build_image_analysis_message(image_url), True

    return query_text, is_image_analysis


def ensure_session_id(session_id: Optional[str]) -> str:
    return session_id or str(uuid.uuid4())


async def prepare_image_analyze_payload(
    image: UploadFile,
    query: Optional[str],
    session_id: Optional[str],
) -> Tuple[str, str, List]:
    resolved_session_id, history = await get_session_history(session_id)
    _image_id, image_url = await save_uploaded_image(image)
    user_message = build_image_analysis_message(image_url, query)
    return resolved_session_id, user_message, history


async def get_session_history(session_id: Optional[str]) -> Tuple[str, List]:
    resolved_session_id = ensure_session_id(session_id)
    history = await _get_message_history(resolved_session_id)
    return resolved_session_id, history
