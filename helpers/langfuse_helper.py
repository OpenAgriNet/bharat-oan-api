import os
from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

from app.config import settings

os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key or ""
os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
os.environ["LANGFUSE_HOST"] = settings.langfuse_host or ""
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or settings.environment

langfuse = Langfuse()