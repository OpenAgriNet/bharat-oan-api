from __future__ import annotations

from agents.model_registry import ModelRegistry, get_registry
from app.config import settings

# Public type alias — any alias name from config/models.yaml.
AgrinetRoute = str

_AGRINET_USE_CASE = "agrinet"
_MODERATION_USE_CASE = "moderation"


def _r() -> ModelRegistry:
    return get_registry()


# Default route used when routing is disabled or for session state repair.
AGRINET_DEFAULT_ROUTE: AgrinetRoute = _r().get_default_alias(_AGRINET_USE_CASE)

# Startup model objects — agrinet gets overridden per-request via get_agrinet_route_model;
# moderation is fixed for all calls.
AGRINET_MODEL = _r().get_model(AGRINET_DEFAULT_ROUTE)
MODERATION_MODEL = _r().get_model(_r().get_default_alias(_MODERATION_USE_CASE))

# Langfuse dashboard labels.
LANGFUSE_AGRINET_MODEL_NAME: str = _r().get_model_name(AGRINET_DEFAULT_ROUTE)
LANGFUSE_MODERATION_MODEL_NAME: str = _r().get_model_name(_r().get_default_alias(_MODERATION_USE_CASE))


def get_agrinet_route_model(route: AgrinetRoute):
    return _r().get_model(route)


def get_agrinet_route_model_name(route: AgrinetRoute) -> str:
    return _r().get_model_name(route)


def validate_agrinet_routing_config() -> None:
    if not settings.agrinet_routing_enabled:
        return
    registry = _r()
    registry.validate_use_case(_AGRINET_USE_CASE)
    # Force-build every agrinet model so bad config (missing env vars) fails at startup.
    for alias in registry.get_use_case_aliases(_AGRINET_USE_CASE):
        registry.get_model(alias)
