from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from app.auth.jwt_auth import get_current_user
from app.core.image_storage import save_uploaded_image, get_image_path, get_image_metadata
from helpers.utils import get_logger
from pydantic import BaseModel

logger = get_logger(__name__)

router = APIRouter(prefix="/image", tags=["image"])


class ImageUploadResponse(BaseModel):
    image_id: str


@router.post("/upload", response_model=ImageUploadResponse)
async def upload_image(
    image: UploadFile = File(..., description="Crop image to upload (JPEG or PNG)"),
    current_user: str = Depends(get_current_user)
):
    """
    Upload a crop image for pest/disease analysis.

    The image is stored temporarily on the backend and only the `image_id`
    is returned to the client.
    After NPSS processing, the image is automatically cleaned up or moved to GCS.
    """
    # Validate mimetype
    allowed = {"image/jpeg", "image/jpg", "image/png"}
    if image.content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image format: {image.content_type}. Allowed: JPEG, PNG."
        )

    image_id, _image_url = await save_uploaded_image(image)

    logger.info(f"Image uploaded by {current_user}: {image_id}")

    return ImageUploadResponse(
        image_id=image_id
    )


@router.get("/{image_id}")
async def serve_image(image_id: str):
    """
    Serve an uploaded image by its ID.

    This endpoint is used by the `analyze_crop_image` tool to download
    the image bytes before forwarding them to the NPSS API.

    NOTE: No auth required — image IDs are UUIDs (unguessable) and
    images are temporary (auto-deleted after processing).
    """
    file_path = get_image_path(image_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="Image not found or expired")

    metadata = get_image_metadata(image_id)
    mimetype = metadata["mimetype"] if metadata else "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=mimetype,
        filename=file_path.name
    )
