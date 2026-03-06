from helpers.utils import get_logger
import os
from dotenv import load_dotenv
from pydantic_ai import Agent
load_dotenv()
logger = get_logger(__name__)


def telemetry_init() -> bool:
    """
    Initialise the Langfuse client and instrument Pydantic AI agents.

    Reads credentials from environment variables:
        - LANGFUSE_SECRET_KEY
        - LANGFUSE_PUBLIC_KEY
        - LANGFUSE_BASE_URL  (optional, defaults to Langfuse cloud)

    Returns:
        True if initialisation and auth succeeded, False otherwise.
    """
    try:
        from langfuse import get_client
    except ImportError:
        logger.error(
            "Langfuse package is not installed. "
            "Run `pip install langfuse` to enable telemetry."
        )
        return False

    try:
        langfuse = get_client()
        logger.info("Langfuse client initialised successfully")
    except Exception:
        logger.exception("Failed to initialise Langfuse client")
        return False

    if not langfuse.auth_check():
        logger.warning(
            "Langfuse authentication failed — check LANGFUSE_SECRET_KEY "
            "and LANGFUSE_PUBLIC_KEY environment variables"
        )
        return False

    logger.info("Langfuse authentication successful (host=%s)", os.getenv("LANGFUSE_BASE_URL", "cloud"))
    _instrument_agents()
    return True


def _instrument_agents() -> None:
    """Attach Langfuse instrumentation to all Pydantic AI agents."""
    try:
        Agent.instrument_all()
        logger.info("Pydantic AI agents instrumented successfully")
    except AttributeError:
        logger.warning("Agent.instrument_all() not available — update pydantic-ai")
    except Exception:
        logger.exception("Pydantic AI agent instrumentation failed")