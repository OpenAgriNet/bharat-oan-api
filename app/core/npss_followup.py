import re
from typing import Any, Optional


NPSS_LOCATION_REQUIRED_MARKER = "[NPSS_LOCATION_REQUIRED]"
INTERNAL_IMAGE_URL_PATTERN = re.compile(r"\[IMAGE_URL:\s*([^\]\s]+)\s*\]")

def find_pending_npss_image_url(history: list[Any]) -> Optional[str]:
    """Find the image URL from the most recent NPSS location-request tool result."""
    for message in reversed(history or []):
        for part in reversed(getattr(message, "parts", []) or []):
            if getattr(part, "part_kind", "") != "tool-return":
                continue
            content = getattr(part, "content", "")
            if not isinstance(content, str):
                continue
            if NPSS_LOCATION_REQUIRED_MARKER in content:
                match = INTERNAL_IMAGE_URL_PATTERN.search(content)
                return match.group(1) if match else None
            if (
                "**NPSS Analysis Result**" in content
                or getattr(part, "tool_name", "") == "analyze_crop_image"
            ):
                return None
    return None
