import re
from typing import Any, Optional


NPSS_LOCATION_REQUIRED_MARKER = "[NPSS_LOCATION_REQUIRED]"
INTERNAL_IMAGE_URL_PATTERN = re.compile(r"\[IMAGE_URL:\s*([^\]\s]+)\s*\]")


def _inspect_npss_tool_part(part: Any) -> tuple[bool, Optional[str]]:
    """Return whether this part decides pending state and its image URL, if any."""
    if getattr(part, "part_kind", "") != "tool-return":
        return False, None

    content = getattr(part, "content", "")
    if not isinstance(content, str):
        return False, None

    if NPSS_LOCATION_REQUIRED_MARKER in content:
        match = INTERNAL_IMAGE_URL_PATTERN.search(content)
        return True, match.group(1) if match else None

    analysis_finished = (
        "**NPSS Analysis Result**" in content
        or getattr(part, "tool_name", "") == "analyze_crop_image"
    )
    return analysis_finished, None


def find_pending_npss_image_url(history: list[Any]) -> Optional[str]:
    """Find the image URL from the most recent NPSS location-request tool result."""
    for message in reversed(history or []):
        for part in reversed(getattr(message, "parts", []) or []):
            decides_pending_state, image_url = _inspect_npss_tool_part(part)
            if decides_pending_state:
                return image_url
    return None
