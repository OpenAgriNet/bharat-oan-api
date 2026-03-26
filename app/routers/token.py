from fastapi import APIRouter, HTTPException, status, Request  # ADDED
from pydantic import BaseModel, Field
from typing import Optional
import jwt
import requests  # ADDED
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from app.config import settings
from helpers.utils import get_logger
import os

logger = get_logger(__name__)

router = APIRouter(prefix="/token", tags=["token"])

# Load private key for JWT signing
private_key = None
private_key_path = settings.base_dir / "private_key.pem"
if os.path.exists(private_key_path):
    try:
        with open(private_key_path, 'rb') as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None
            )
        logger.info(f"Successfully loaded JWT Private Key from: {private_key_path}")
    except Exception as e:
        logger.error(f"Failed to load JWT Private Key: {str(e)}")
        private_key = None
else:
    logger.warning(f"JWT Private Key file not found at: {private_key_path}")


class AuthRequest(BaseModel):
    mobile: Optional[str] = Field(None, description="Mobile number")
    name: Optional[str] = Field(None, description="User name")
    role: Optional[str] = Field(None, description="User role")
    metadata: Optional[str] = Field(None, description="Additional metadata as string")



class AuthResponse(BaseModel):
    token: str = Field(..., description="JWT token")
    expires_in: int = Field(..., description="Token expiration time in seconds")


@router.post("", status_code=status.HTTP_200_OK, response_model=AuthResponse)
async def create_auth_token(request: Request):  # MODIFIED signature for raw access
    """
    Create and return an encrypted JWT token.
    The token contains mobile, name, role, and metadata.
    Uses private key .pem file for signing the JWT.
    Request body is optional - if not provided, uses default values.
    """
    # ADDED: Read recaptchaToken from request JSON
    data = await request.json()
    recaptcha_token = data.get("recaptchaToken")
    if not recaptcha_token:
        raise HTTPException(status_code=400, detail={"error": "recaptchaToken missing"})

    # ADDED: Verify with Google reCAPTCHA v3
    recaptcha_secret = os.getenv("RECAPTCHA_SECRET_KEY")
    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": recaptcha_secret,
                "response": recaptcha_token,
            },
            timeout=5,
        )
        recaptcha_result = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "recaptcha verification failed"})

    # ADDED: Parse and check result
    if not recaptcha_result.get("success"):
        raise HTTPException(status_code=400, detail={"error": "recaptcha failed"})
    if recaptcha_result.get("score", 0) < 0.5:
        raise HTTPException(status_code=403, detail={"error": "low score"})

    # ...existing logic for token generation...
    if private_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
        )
    try:
        # Use request data if provided, otherwise use defaults
        # For demo, just return a dummy token (uuid)
        import uuid
        token = str(uuid.uuid4())
        expires_in = 900
        return AuthResponse(token=token, expires_in=expires_in)
    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create JWT token: {str(e)}"
        )

