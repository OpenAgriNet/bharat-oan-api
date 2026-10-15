"""
Temporary image storage module for uploaded crop images.

Handles:
- Saving uploaded images to a temp directory
- Serving images via a local URL
- Cleaning up after NPSS processing
- Moving processed images to a mounted GCS bucket (optional)

The agent NEVER sees raw image bytes — only a URL string.
"""
import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from helpers.utils import get_logger
from fastapi import HTTPException, UploadFile

logger = get_logger(__name__)

# Configuration
APP_BASE_DIR = Path(__file__).resolve().parents[2]
TEMP_UPLOAD_DIR = Path(
    os.getenv("TEMP_UPLOAD_DIR") or APP_BASE_DIR / ".oan-uploads"
).expanduser().resolve()
GCS_MOUNT_PATH = os.getenv("GCS_MOUNT_PATH", "")  # e.g. /mnt/gcs-bucket/crop-images
GCS_MOUNT_DIR = Path(GCS_MOUNT_PATH).expanduser().resolve() if GCS_MOUNT_PATH else None
IMAGE_TTL_MINUTES = int(os.getenv("IMAGE_TTL_MINUTES", "60"))
BASE_URL = os.getenv("BASE_URL", "")  # e.g. https://api.example.com — used to build absolute URLs
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_IMAGE_UPLOAD_SIZE_MB", "10"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
JPEG_MIMETYPE = "image/jpeg"
OCTET_STREAM_MIMETYPE = "application/octet-stream"
JPEG_EXTENSION = ".jpeg"
WEBP_EXTENSION = ".webp"
IMAGE_EXTENSIONS = (".jpg", JPEG_EXTENSION, ".png", WEBP_EXTENSION, ".bin")

# Ensure temp directory exists
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory registry of uploaded images
# Structure: {image_id: {"path": Path, "created_at": datetime, "mimetype": str}}
_upload_registry: dict = {}


def _normalize_image_id(image_id: str) -> str:
    return str(uuid.UUID(str(image_id)))


def _safe_child_path(base_dir: Path, filename: str) -> Path:
    candidate = (base_dir / filename).resolve()
    candidate.relative_to(base_dir)
    return candidate


def _is_relative_to(path: Path, base_dir: Path) -> bool:
    try:
        path.relative_to(base_dir)
        return True
    except ValueError:
        return False


def _metadata_path(image_id: str) -> Path:
    safe_id = _normalize_image_id(image_id)
    return _safe_child_path(TEMP_UPLOAD_DIR, f"{safe_id}.json")


def _guess_mimetype_from_path(file_path: Path) -> str:
    mapping = {
        ".jpg": JPEG_MIMETYPE,
        JPEG_EXTENSION: JPEG_MIMETYPE,
        ".png": "image/png",
        WEBP_EXTENSION: "image/webp",
    }
    return mapping.get(file_path.suffix.lower(), OCTET_STREAM_MIMETYPE)


def _serialize_metadata(entry: dict) -> dict:
    return {
        **entry,
        "path": str(entry["path"]),
        "created_at": entry["created_at"].isoformat(),
    }


def _deserialize_metadata(data: dict) -> dict:
    stored_path = Path(data["path"]).expanduser().resolve()
    allowed_roots = [TEMP_UPLOAD_DIR]
    if GCS_MOUNT_DIR:
        allowed_roots.append(GCS_MOUNT_DIR)
    if not any(_is_relative_to(stored_path, root) for root in allowed_roots):
        raise ValueError("Image metadata path is outside allowed storage directories")

    created_at = datetime.fromisoformat(data["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return {
        **data,
        "path": stored_path,
        "created_at": created_at,
    }


def _persist_metadata(image_id: str, entry: dict) -> None:
    metadata_path = _metadata_path(image_id)
    metadata_path.write_text(json.dumps(_serialize_metadata(entry)), encoding="utf-8")


def _load_metadata(image_id: str) -> Optional[dict]:
    try:
        metadata_path = _metadata_path(image_id)
        if not metadata_path.exists():
            return None
        return _deserialize_metadata(json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to load image metadata for {image_id}: {exc}")
        return None


def _delete_metadata(image_id: str) -> None:
    try:
        metadata_path = _metadata_path(image_id)
    except ValueError:
        return
    if metadata_path.exists():
        metadata_path.unlink()


def _get_extension_from_mimetype(mimetype: str) -> str:
    """Map common image mimetypes to file extensions."""
    mapping = {
        JPEG_MIMETYPE: ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": WEBP_EXTENSION,
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
    image_id = _normalize_image_id(str(uuid.uuid4()))
    ext = _get_extension_from_mimetype(upload_file.content_type or OCTET_STREAM_MIMETYPE)
    file_path = _safe_child_path(TEMP_UPLOAD_DIR, f"{image_id}{ext}")

    # Write file to disk with a hard size limit so large uploads do not exhaust memory/disk.
    total_bytes = 0
    chunks: list[bytes] = []
    try:
        while chunk := await upload_file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Image too large. Maximum allowed size is {MAX_UPLOAD_SIZE_MB} MB.",
                )
            chunks.append(chunk)
        await asyncio.to_thread(file_path.write_bytes, b"".join(chunks))
    except Exception:
        if file_path.exists():
            file_path.unlink()
        raise

    entry = {
        "path": file_path,
        "created_at": datetime.now(timezone.utc),
        "mimetype": upload_file.content_type or OCTET_STREAM_MIMETYPE,
        "original_name": upload_file.filename or "unknown",
        "processed": False,
    }
    _upload_registry[image_id] = entry
    _persist_metadata(image_id, entry)

    image_url = _build_image_url(image_id)
    logger.info(f"Saved uploaded image {image_id} ({total_bytes} bytes) to {file_path}")
    return image_id, image_url


def get_image_path(image_id: str) -> Optional[Path]:
    """Get the filesystem path for an image_id if it exists in temp storage."""
    try:
        safe_id = _normalize_image_id(image_id)
    except ValueError:
        return None

    entry = _upload_registry.get(safe_id)
    if entry and entry["path"].exists():
        return entry["path"]

    # Fallback: check if file exists on disk even if not in registry (e.g. after restart)
    for ext in IMAGE_EXTENSIONS:
        candidate = _safe_child_path(TEMP_UPLOAD_DIR, f"{safe_id}{ext}")
        if candidate.exists():
            return candidate

    # Check GCS mount
    if GCS_MOUNT_DIR:
        for ext in IMAGE_EXTENSIONS:
            candidate = _safe_child_path(GCS_MOUNT_DIR, f"{safe_id}{ext}")
            if candidate.exists():
                return candidate

    return None


def get_image_metadata(image_id: str) -> Optional[dict]:
    """Get metadata for an image."""
    try:
        safe_id = _normalize_image_id(image_id)
    except ValueError:
        return None

    entry = _upload_registry.get(safe_id)
    if entry:
        return entry

    entry = _load_metadata(safe_id)
    if entry:
        _upload_registry[safe_id] = entry
        return entry

    file_path = get_image_path(safe_id)
    if not file_path:
        return None

    return {
        "path": file_path,
        "created_at": datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
        "mimetype": _guess_mimetype_from_path(file_path),
        "original_name": file_path.name,
        "processed": False,
    }


def mark_processed(image_id: str) -> None:
    """Mark an image as processed by NPSS."""
    try:
        safe_id = _normalize_image_id(image_id)
    except ValueError:
        return

    entry = get_image_metadata(safe_id)
    if entry:
        entry["processed"] = True
        _upload_registry[safe_id] = entry
        _persist_metadata(safe_id, entry)
        logger.info(f"Marked image {safe_id} as processed")


def cleanup_image(image_id: str, move_to_gcs: bool = False) -> bool:
    """
    Clean up an image after processing.

    Args:
        image_id: The image ID
        move_to_gcs: If True and GCS_MOUNT_PATH is set, move to GCS instead of deleting

    Returns:
        True if cleanup succeeded
    """
    try:
        safe_id = _normalize_image_id(image_id)
    except ValueError:
        logger.warning(f"Cleanup requested for invalid image_id: {image_id}")
        return False

    entry = get_image_metadata(safe_id)
    file_path = entry["path"] if entry else get_image_path(safe_id)
    _upload_registry.pop(safe_id, None)
    if not file_path:
        logger.warning(f"Cleanup requested for unknown image_id: {safe_id}")
        _delete_metadata(safe_id)
        return False

    if not file_path.exists():
        logger.warning(f"Image file already gone: {file_path}")
        _delete_metadata(safe_id)
        return True

    if move_to_gcs and GCS_MOUNT_DIR:
        try:
            gcs_dir = GCS_MOUNT_DIR
            gcs_dir.mkdir(parents=True, exist_ok=True)
            dest = _safe_child_path(gcs_dir, file_path.name)
            shutil.move(str(file_path), str(dest))
            logger.info(f"Moved image {safe_id} to GCS: {dest}")
            _delete_metadata(safe_id)
            return True
        except Exception as e:
            logger.error(f"Failed to move image {safe_id} to GCS: {e}")
            # Fall through to delete

    try:
        file_path.unlink()
        logger.info(f"Deleted temp image {safe_id}: {file_path}")
        _delete_metadata(safe_id)
        return True
    except Exception as e:
        logger.error(f"Failed to delete image {image_id}: {e}")
        return False


def cleanup_expired_images() -> int:
    """
    Remove images older than IMAGE_TTL_MINUTES that have been processed.
    Returns count of cleaned images.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=IMAGE_TTL_MINUTES)
    to_clean = []
    for metadata_file in TEMP_UPLOAD_DIR.glob("*.json"):
        image_id = metadata_file.stem
        try:
            image_id = _normalize_image_id(image_id)
        except ValueError:
            continue
        entry = _load_metadata(image_id)
        if entry and entry.get("processed") and entry["created_at"] < cutoff:
            to_clean.append(image_id)

    count = 0
    for image_id in to_clean:
        if cleanup_image(image_id, move_to_gcs=False):
            count += 1
    logger.info(f"Cleaned up {count} expired processed images")
    return count
