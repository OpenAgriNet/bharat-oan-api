from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
import jwt
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
async def create_auth_token(request: Optional[AuthRequest] = None):
    """
    Create and return an encrypted JWT token.
    The token contains mobile, name, role, and metadata.
    Uses private key .pem file for signing the JWT.
    Request body is optional - if not provided, uses default values.
    """
    if private_key is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT private key is not configured. Please ensure private_key.pem file exists in the project root."
        )
    
    try:
        # Use request data if provided, otherwise use defaults
        mobile = request.mobile if request and request.mobile else "1111111111"
        name = request.name if request and request.name else "guest"
        role = request.role if request and request.role else "public"
        metadata = request.metadata if request and request.metadata else ""
        
        # Create JWT payload
        now = datetime.utcnow()
        exp = now + timedelta(days=30)
        
        payload = {
            "mobile": mobile,
            "name": name,
            "role": role,
            "metadata": metadata,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp())
        }
        
        # Encode JWT token using private key
        token = jwt.encode(
            payload,
            private_key,
            algorithm=settings.jwt_algorithm
        )
        
        logger.info(f"JWT token created successfully for mobile: {mobile}, role: {role}")
        
        # Calculate expiration time in seconds
        expires_in = int(exp.timestamp() - now.timestamp())
        
        return AuthResponse(
            token=token,
            expires_in=expires_in
        )
        
    except Exception as e:
        logger.error(f"Error creating JWT token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create JWT token: {str(e)}"
        )

