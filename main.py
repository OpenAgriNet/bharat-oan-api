import asyncio
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from app.config import settings
from app.core.cache import cache
from agents.models import validate_agrinet_routing_config
from contextlib import asynccontextmanager

load_dotenv()

# Set root/app log level from config so INFO logs (e.g. Bhashini, TTS, Transcribe) appear
_log_level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
logging.basicConfig(level=_log_level, format=settings.log_format, force=True)
for _name in ("helpers.transcription", "helpers.tts", "app.tasks.telemetry", "app.routers.transcribe", "app.routers.tts"):
    logging.getLogger(_name).setLevel(_log_level)

logger = logging.getLogger(__name__)

# Import all routers
from app.routers import chat, transcribe, tts, health, file, token, telemetry
# from app.routers import suggestions  # Commented out: suggestion agent disabled

class TimingAllowOriginMiddleware:
    """Add Timing-Allow-Origin to every response.

    Deliberately plain ASGI rather than BaseHTTPMiddleware: BaseHTTPMiddleware runs
    the rest of the app in its own task and cancel scope, which pumps the streaming
    chat response through an extra memory stream and turns a client disconnect into
    cross-task cancellation noise. This only rewrites a response header, so it has no
    reason to sit between the server and the response body.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_timing_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Timing-Allow-Origin accepts "*" or a single origin
                origin = "*" if "*" in settings.allowed_origins or len(settings.allowed_origins) != 1 else settings.allowed_origins[0]
                MutableHeaders(scope=message)["Timing-Allow-Origin"] = origin
            await send(message)

        await self.app(scope, receive, send_with_timing_header)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events for startup and shutdown"""
    # Startup
    token.validate_multi_provider_auth_config()
    validate_agrinet_routing_config()
    print(f"🚀 {settings.app_name} starting up...")
    print(f"📍 Environment: {settings.environment}")
    print(f"🔧 Debug mode: {settings.debug}")
    print(f"🌐 CORS origins: {settings.allowed_origins}")
    if settings.qdrant_url:
        from helpers.scheme_qdrant_search import warm_scheme_search

        logger.info("Pre-warming scheme search embedder...")
        await asyncio.to_thread(warm_scheme_search)
    yield
    # Shutdown
    print(f"🛑 {settings.app_name} shutting down...")

# Create FastAPI app with settings
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    description="AI-powered Voice Assistant API for Agricultural Support",
    lifespan=lifespan
)

# Add CORS middleware with enhanced settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=settings.allowed_credentials,
    allow_methods=settings.allowed_methods,
    allow_headers=settings.allowed_headers,
    expose_headers=["X-QID"],
)

# Add Timing-Allow-Origin middleware for cross-origin timing access
app.add_middleware(TimingAllowOriginMiddleware)


@app.get("/")
async def root():
    """Root endpoint with app information"""
    return {
        "app": settings.app_name,
        "environment": settings.environment,
        "debug": settings.debug,
        "api_prefix": settings.api_prefix
    }

# Include all routers with API prefix from settings
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(transcribe.router, prefix=settings.api_prefix)
# app.include_router(suggestions.router, prefix=settings.api_prefix)  # Commented out: suggestion agent disabled
app.include_router(tts.router, prefix=settings.api_prefix)
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(file.router, prefix=settings.api_prefix)
app.include_router(token.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
