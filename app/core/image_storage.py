"""
Temporary image storage module for uploaded crop images.

Handles:
- Saving uploaded images to a temp directory
- Serving images via a local URL
- Cleaning up after NPSS processing
- Moving processed images to a mounted GCS bucket (optional)

The agent NEVER sees raw image bytes — only a URL string.
"""
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
from helpers.utils import get_logger
from fastapi import UploadFile

logger = get_logger(__name__)

# Configuration
TEMP_UPLOAD_DIR = Path(os.getenv("TEMP_UPLOAD_DIR", "/tmp/oan-uploads"))
GCS_MOUNT_PATH = os.getenv("GCS_MOUNT_PATH", "")  # e.g. /mnt/gcs-bucket/crop-images
IMAGE_TTL_MINUTES = int(os.getenv("IMAGE_TTL_MINUTES", "60"))
BASE_URL = os.getenv("BASE_URL", "")  # e.g. https://api.example.com — used to build absolute URLs

# Ensure temp directory exists
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory registry of uploaded images
# Structure: {image_id: {"path": Path, "created_at": datetime, "mimetype": str}}
_upload_registry: dict = {}


def _metadata_path(image_id: str) -> Path:
    return TEMP_UPLOAD_DIR / f"{image_id}.json"


def _guess_mimetype_from_path(file_path: Path) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    return mapping.get(file_path.suffix.lower(), "application/octet-stream")


def _serialize_metadata(entry: dict) -> dict:
    return {
        **entry,
        "path": str(entry["path"]),
        "created_at": entry["created_at"].isoformat(),
    }


def _deserialize_metadata(data: dict) -> dict:
    return {
        **data,
        "path": Path(data["path"]),
        "created_at": datetime.fromisoformat(data["created_at"]),
    }


def _persist_metadata(image_id: str, entry: dict) -> None:
    metadata_path = _metadata_path(image_id)
    metadata_path.write_text(json.dumps(_serialize_metadata(entry)), encoding="utf-8")


def _load_metadata(image_id: str) -> Optional[dict]:
    metadata_path = _metadata_path(image_id)
    if not metadata_path.exists():
        return None

    try:
        return _deserialize_metadata(json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to load image metadata for {image_id}: {exc}")
        return None


def _delete_metadata(image_id: str) -> None:
    metadata_path = _metadata_path(image_id)
    if metadata_path.exists():
        metadata_path.unlink()


def _get_extension_from_mimetype(mimetype: str) -> str:
    """Map common image mimetypes to file extensions."""
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    return mapping.get(mimetype, ".bin")


def _build_image_url(image_id: str) -> str:
    """Build the public URL for an image."""
    if BASE_URL:
        return f"{BASE_URL.rstrip('/')}/api/image/{image_id}"
    # Relative URL — frontend must resolve against same origin
    return f"/api/image/{image_id}"


async def save_uploaded_image(upload_file: UploadFile) -> Tuple[str, str]:
    """
    Save an uploaded image to temp storage and return its ID and public URL.

    Args:
        upload_file: FastAPI UploadFile

    Returns:
        Tuple of (image_id, image_url)
    """
    image_id = str(uuid.uuid4())
    ext = _get_extension_from_mimetype(upload_file.content_type or "application/octet-stream")
    file_path = TEMP_UPLOAD_DIR / f"{image_id}{ext}"

    # Write file to disk
    content = await upload_file.read()
    file_path.write_bytes(content)

    entry = {
        "path": file_path,
        "created_at": datetime.utcnow(),
        "mimetype": upload_file.content_type or "application/octet-stream",
        "original_name": upload_file.filename or "unknown",
        "processed": False,
    }
    _upload_registry[image_id] = entry
    _persist_metadata(image_id, entry)

    image_url = _build_image_url(image_id)
    logger.info(f"Saved uploaded image {image_id} ({len(content)} bytes) to {file_path}")
    return image_id, image_url


def get_image_path(image_id: str) -> Optional[Path]:
    """Get the filesystem path for an image_id if it exists in temp storage."""
    entry = _upload_registry.get(image_id)
    if entry and entry["path"].exists():
        return entry["path"]

    # Fallback: check if file exists on disk even if not in registry (e.g. after restart)
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bin"):
        candidate = TEMP_UPLOAD_DIR / f"{image_id}{ext}"
        if candidate.exists():
            return candidate

    # Check GCS mount
    if GCS_MOUNT_PATH:
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".bin"):
            candidate = Path(GCS_MOUNT_PATH) / f"{image_id}{ext}"
            if candidate.exists():
                return candidate

    return None


def get_image_metadata(image_id: str) -> Optional[dict]:
    """Get metadata for an image."""
    entry = _upload_registry.get(image_id)
    if entry:
        return entry

    entry = _load_metadata(image_id)
    if entry:
        _upload_registry[image_id] = entry
        return entry

    file_path = get_image_path(image_id)
    if not file_path:
        return None

    return {
        "path": file_path,
        "created_at": datetime.utcfromtimestamp(file_path.stat().st_mtime),
        "mimetype": _guess_mimetype_from_path(file_path),
        "original_name": file_path.name,
        "processed": False,
    }


def mark_processed(image_id: str) -> None:
    """Mark an image as processed by NPSS."""
    entry = get_image_metadata(image_id)
    if entry:
        entry["processed"] = True
        _upload_registry[image_id] = entry
        _persist_metadata(image_id, entry)
        logger.info(f"Marked image {image_id} as processed")


def cleanup_image(image_id: str, move_to_gcs: bool = False) -> bool:
    """
    Clean up an image after processing.

    Args:
        image_id: The image ID
        move_to_gcs: If True and GCS_MOUNT_PATH is set, move to GCS instead of deleting

    Returns:
        True if cleanup succeeded
    """
    entry = get_image_metadata(image_id)
    file_path = entry["path"] if entry else get_image_path(image_id)
    _upload_registry.pop(image_id, None)
    if not file_path:
        logger.warning(f"Cleanup requested for unknown image_id: {image_id}")
        _delete_metadata(image_id)
        return False

    if not file_path.exists():
        logger.warning(f"Image file already gone: {file_path}")
        _delete_metadata(image_id)
        return True

    if move_to_gcs and GCS_MOUNT_PATH:
        try:
            gcs_dir = Path(GCS_MOUNT_PATH)
            gcs_dir.mkdir(parents=True, exist_ok=True)
            dest = gcs_dir / file_path.name
            shutil.move(str(file_path), str(dest))
            logger.info(f"Moved image {image_id} to GCS: {dest}")
            _delete_metadata(image_id)
            return True
        except Exception as e:
            logger.error(f"Failed to move image {image_id} to GCS: {e}")
            # Fall through to delete

    try:
        file_path.unlink()
        logger.info(f"Deleted temp image {image_id}: {file_path}")
        _delete_metadata(image_id)
        return True
    except Exception as e:
        logger.error(f"Failed to delete image {image_id}: {e}")
        return False


def cleanup_expired_images() -> int:
    """
    Remove images older than IMAGE_TTL_MINUTES that have been processed.
    Returns count of cleaned images.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=IMAGE_TTL_MINUTES)
    to_clean = []
    for metadata_file in TEMP_UPLOAD_DIR.glob("*.json"):
        image_id = metadata_file.stem
        entry = _load_metadata(image_id)
        if entry and entry.get("processed") and entry["created_at"] < cutoff:
            to_clean.append(image_id)

    count = 0
    for image_id in to_clean:
        if cleanup_image(image_id, move_to_gcs=False):
            count += 1
    logger.info(f"Cleaned up {count} expired processed images")
    return count
