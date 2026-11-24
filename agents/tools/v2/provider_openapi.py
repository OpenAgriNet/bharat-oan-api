"""A provider's native OpenAPI, read through a catalog resource's select operationRef.
Shared by the v2 tools whose select request follows the provider's own fields."""
import os
import time
from typing import Any, Dict, Tuple

import httpx
import yaml

from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.utils import get_logger

logger = get_logger(__name__)

_OPENAPI_CACHE_TTL_SECONDS_DEFAULT = 3600
# Each fetched OpenAPI document by URL, with the monotonic time it stops being reused.
_OPENAPI_CACHE: Dict[str, Tuple[float, dict]] = {}


class SchemaUnavailable(Exception):
    """The select operation's request schema could not be fetched or read."""


def _cache_ttl_seconds() -> float:
    return float(os.getenv("PROVIDER_OPENAPI_CACHE_TTL_SECONDS", _OPENAPI_CACHE_TTL_SECONDS_DEFAULT))


async def openapi_document(url: str) -> dict:
    cached = _OPENAPI_CACHE.get(url)
    if cached and cached[0] > time.monotonic():
        logger.info(f"Reusing cached provider OpenAPI {url}")
        return cached[1]
    logger.info(f"Fetching provider OpenAPI {url}")
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(url)
        response.raise_for_status()
        document = yaml.safe_load(response.text)
    except (httpx.HTTPError, yaml.YAMLError) as e:
        raise SchemaUnavailable(f"fetch failed: {e}") from e
    if not isinstance(document, dict):
        raise SchemaUnavailable("not an OpenAPI document")
    _OPENAPI_CACHE[url] = (time.monotonic() + _cache_ttl_seconds(), document)
    return document


def _follow_pointer(document: dict, pointer: str) -> Any:
    node: Any = document
    for part in pointer.strip("/").split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise SchemaUnavailable(f"{pointer} has no {key!r}")
        node = node[key]
    return node


def _resolved(document: dict, node: Any) -> Any:
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/") or ref in seen:
            raise SchemaUnavailable(f"cannot follow $ref {ref}: only local, non-circular refs are read")
        seen.add(ref)
        node = _follow_pointer(document, ref[1:])
    return node


def request_schema(document: dict, pointer: str) -> dict:
    """The JSON request body schema of the operation at ``pointer``, with local refs followed."""
    operation = _resolved(document, _follow_pointer(document, pointer))
    body = _resolved(document, operation.get("requestBody") or {})
    media = (body.get("content") or {}).get("application/json") or {}
    schema = _resolved(document, media.get("schema") or {})
    properties = {name: _resolved(document, prop) for name, prop in (schema.get("properties") or {}).items()}
    if not properties:
        raise SchemaUnavailable(f"{pointer} has no JSON request fields")
    return {**schema, "properties": properties}


def select_operation_ref(resource: dict) -> str:
    for action in (resource.get("resourceAttributes") or {}).get("actions") or []:
        if action.get("action") == "select" and action.get("operationRef"):
            return action["operationRef"]
    raise SchemaUnavailable(f"{resource.get('id')} has no select operationRef")
