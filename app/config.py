import os
from pathlib import Path
from typing import List, Optional
import httpx
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    # Core Application Settings
    app_name: str = "Bharatvistaar AI API"
    environment: str = os.getenv("ENVIRONMENT", "production")
    debug: bool = False
    base_dir: Path = Path(__file__).resolve().parent.parent
    secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    timezone: str = "Asia/Kolkata"

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"
    rate_limit_requests_per_minute: int = 1000

    # Security Settings
    allowed_origins: List[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    allowed_credentials: bool = True
    allowed_methods: List[str] = ["*"]
    allowed_headers: List[str] = ["*"]
    disable_auth: bool = (
        os.getenv("DISABLE_AUTH", "").strip().lower() in ("1", "true", "yes", "y", "on")
        or os.getenv("ENVIRONMENT", "production").strip().lower() in ("local", "dev", "development")
    )
    disable_auth_mobile: str = os.getenv("DISABLE_AUTH_MOBILE", "9999999999")

    # JWT Configuration
    jwt_algorithm: str = "RS256"
    jwt_public_key_path: str = os.getenv("JWT_PUBLIC_KEY_PATH", "jwt_public_key.pem")
    jwt_private_key_path: Optional[str] = os.getenv("JWT_PRIVATE_KEY_PATH")
    jwt_expiry_minutes: int = int(os.getenv("JWT_EXPIRY_MINUTES", "15"))
    disable_jwt: bool = (
        os.getenv("DISABLE_JWT", "").strip().lower() in ("1", "true", "yes", "on")
        or os.getenv("DISABLE_JWT_FOR_LOCAL", "").strip().lower() in ("1", "true", "yes", "on")
    )

    # API Key Auth Configuration
    api_key_auth_token: Optional[str] = os.getenv("API_KEY_AUTH_TOKEN")
    api_key_auth_token_prefix: str = os.getenv("API_KEY_AUTH_TOKEN_PREFIX", "API_KEY_AUTH_TOKEN_")

    # Play Integrity Auth Configuration
    play_integrity_package_name: Optional[str] = os.getenv("PLAY_INTEGRITY_PACKAGE_NAME")
    play_integrity_service_account_file: Optional[str] = os.getenv("PLAY_INTEGRITY_SERVICE_ACCOUNT_FILE")
    play_integrity_service_account_base_path: Optional[str] = os.getenv("PLAY_INTEGRITY_SERVICE_ACCOUNT_BASE_PATH")
    play_integrity_package_name_prefix: str = os.getenv("PLAY_INTEGRITY_PACKAGE_NAME_PREFIX", "PLAY_INTEGRITY_PACKAGE_NAME_")
    play_integrity_private_key_prefix: str = os.getenv("PLAY_INTEGRITY_PRIVATE_KEY_PREFIX", "PLAY_INTEGRITY_PRIVATE_KEY_")
    play_integrity_freshness_seconds: int = int(os.getenv("PLAY_INTEGRITY_FRESHNESS_SECONDS", "120"))
    play_integrity_default_name: str = os.getenv("PLAY_INTEGRITY_DEFAULT_NAME", "android_user")
    play_integrity_default_role: str = os.getenv("PLAY_INTEGRITY_DEFAULT_ROLE", "public")
    play_integrity_default_metadata: str = os.getenv("PLAY_INTEGRITY_DEFAULT_METADATA", "")

    # Worker Settings
    uvicorn_workers: int = os.cpu_count() or 1

    # Redis Settings
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_key_prefix: str = "sva-cache-"
    redis_socket_connect_timeout: int = 10
    redis_socket_timeout: int = 10
    redis_max_connections: int = 100
    redis_retry_on_timeout: bool = True

    # Cache Configuration
    default_cache_ttl: int = 60 * 60 * 24  # 24 hours
    suggestions_cache_ttl: int = 60 * 30    # 30 minutes

    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # External Service URLs
    telemetry_api_url: str = os.getenv("TELEMETRY_API_URL", "https://dev-vistaar.da.gov.in/observability-service/action/data/v3/telemetry")
    bhashini_api_url: str = ""
    ollama_endpoint_url: Optional[str] = None
    marqo_endpoint_url: Optional[str] = None
    inference_endpoint_url: Optional[str] = None

    # External Service API Keys
    openai_api_key: Optional[str] = None
    sarvam_api_key: Optional[str] = None
    meity_api_key_value: Optional[str] = None
    logfire_token: Optional[str] = None
    bhashini_api_key: str = ""
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: Optional[str] = None
    # Langfuse UI "Env" badge + OTel resource; defaults to app ENVIRONMENT so it matches env: tags in chat traces.
    langfuse_tracing_environment: str = (
        os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or os.getenv("ENVIRONMENT", "production")
    )
    eleven_labs_api_key: str = ""
    inference_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    mapbox_api_token: Optional[str] = None

    # AWS Configuration
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_s3_bucket: Optional[str] = None

    # LLM Configuration
    llm_provider: Optional[str] = None
    llm_model_name: Optional[str] = None
    marqo_index_name: Optional[str] = None
    marqo_pests_diseases_index_name: Optional[str] = None

    # HTTP client timeouts for outbound API calls (connect and read; read should be > connect)
    default_api_timeout: float = 5.0   # connect timeout (DEFAULT_API_TIMEOUT)
    default_api_read_timeout: float = 10.0  # read timeout (DEFAULT_API_READ_TIMEOUT)

    # NPSS Configuration
    npss_base_url: Optional[str] = os.getenv("NPSS_BASE_URL")
    npss_username: Optional[str] = os.getenv("NPSS_USERNAME")
    npss_password: Optional[str] = os.getenv("NPSS_PASSWORD")

    # Image Storage Configuration
    temp_upload_dir: str = os.getenv("TEMP_UPLOAD_DIR", "/tmp/oan-uploads")
    gcs_mount_path: Optional[str] = os.getenv("GCS_MOUNT_PATH")
    image_ttl_minutes: int = int(os.getenv("IMAGE_TTL_MINUTES", "60"))
    base_url: Optional[str] = os.getenv("BASE_URL")
    gcs_move_after_process: bool = os.getenv("GCS_MOVE_AFTER_PROCESS", "").strip().lower() in ("1", "true", "yes", "on")

    class Config:
        env_file = ".env"
        extra = 'ignore'  # Ignore extra fields from .env

settings = Settings()


def get_default_httpx_timeout() -> httpx.Timeout:
    """Default timeout for outbound API calls. Connect from DEFAULT_API_TIMEOUT, read from DEFAULT_API_READ_TIMEOUT (read > connect)."""
    return httpx.Timeout(settings.default_api_timeout, read=settings.default_api_read_timeout)


# Backward compatibility for modules importing the previous constant.
DEFAULT_HTTP_TIMEOUT = get_default_httpx_timeout()
