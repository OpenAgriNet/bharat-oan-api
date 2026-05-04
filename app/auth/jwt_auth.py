import jwt
import os
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from helpers.utils import get_logger
from app.config import settings # Import the application settings

load_dotenv()

logger = get_logger(__name__)

# OAuth2 scheme for FastAPI - always requires Bearer token (no env-based bypass)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Construct the absolute path to the public key using settings
public_key_path = settings.base_dir / settings.jwt_public_key_path

with open(public_key_path, 'rb') as key_file:
    public_key = serialization.load_pem_public_key(key_file.read())
logger.info(f"Successfully loaded JWT Public Key from: {public_key_path}")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    FastAPI dependency to get current authenticated user from JWT token.
    Always validates the JWT; no environment-based bypass.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if public_key is None:
        logger.error("JWT Public Key is not loaded, cannot verify tokens.")
        raise credentials_exception
        
    try:
        decoded_token = jwt.decode(
            token,
            public_key,
            algorithms=[settings.jwt_algorithm],
            options={
                "verify_signature": True,
                "verify_aud": False,
                "verify_iss": False
            }
        )
        
        logger.info("Successfully decoded token")
        mobile = decoded_token.get('mobile')
        if mobile is None:
            logger.warning("No mobile number found in token")
#            raise credentials_exception
            
        return mobile
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    except Exception as e:
        logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )