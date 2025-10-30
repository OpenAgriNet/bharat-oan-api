from fastapi import APIRouter, HTTPException, Response
from app.core.cache import cache
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/file", tags=["file"])


def generate_cache_key(file_hash: str) -> str:
    """Generate cache key from file hash (same as in shc_scheme_status.py).
    
    Args:
        file_hash (str): The file hash
        
    Returns:
        str: Cache key for retrieving HTML content
    """
    return f"html_file:{file_hash}"


@router.get("/{file_hash}")
async def serve_html_file(file_hash: str):
    """
    Serve cached HTML file by hash.
    
    Args:
        file_hash (str): The hash of the cached HTML file
        
    Returns:
        Response: HTML content with appropriate headers
    """
    try:
        # Generate cache key
        cache_key = generate_cache_key(file_hash)
        
        # Retrieve HTML content from cache
        html_content = await cache.get(cache_key)
        
        if html_content is None:
            logger.warning(f"HTML file not found in cache for hash: {file_hash}")
            raise HTTPException(status_code=404, detail="File not found or expired")
        
        logger.info(f"Serving cached HTML file with hash: {file_hash}")
        
        # Return HTML content with appropriate headers
        return Response(
            content=html_content,
            media_type="text/html",
            headers={
                "Cache-Control": "public, max-age=600",  # Cache for 10 minutes
                "Content-Type": "text/html; charset=utf-8"
            }
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error serving HTML file {file_hash}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")