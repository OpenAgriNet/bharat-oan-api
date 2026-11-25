"""A provider's native OpenAPI, read through a catalog resource's operationRef.
Shared by the v2 tools whose request follows the provider's own fields."""
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

# Where a request field's value comes from, declared on the field itself. Without
# it a tool has to be built already knowing which of its own values belongs in
# which of the provider's field names.
VALUE_SOURCE = "x-oan-from"

# A sealed-envelope provider declares its wire body as the envelope, so
# requestBody says nothing about fields. This names the attributes a Beckn call
# has to carry instead.
REQUEST_ATTRIBUTES = "x-oan-request-attributes"


class SchemaUnavailable(Exception):
    """The operation's request schema could not be fetched or read."""


def _cache_ttl_seconds() -> float:
    return float(os.getenv("PROVIDER_OPENAPI_CACHE_TTL_SECONDS", _OPENAPI_CACHE_TTL_SECONDS_DEFAULT))


def rewritten_url(url: str) -> str:
    """The URL to actually fetch.

    A catalog names the published spec, which is right for everyone reading it
    and wrong for a deployment that cannot reach the public site -- an offline
    stack, or one testing a spec change before it is published. The pairs in
    PROVIDER_OPENAPI_URL_REWRITE ("from=to,from=to") redirect it to the same
    file served somewhere else. Deployment wiring, like the provider base URL:
    nothing in the catalog or the tool changes.
    """
    for pair in os.getenv("PROVIDER_OPENAPI_URL_REWRITE", "").split(","):
        prefix, _, replacement = pair.strip().partition("=")
        if prefix and url.startswith(prefix):
            rewritten = replacement + url[len(prefix):]
            logger.info(f"Reading {url} from {rewritten}")
            return rewritten
    return url


async def openapi_document(url: str) -> dict:
    cached = _OPENAPI_CACHE.get(url)
    if cached and cached[0] > time.monotonic():
        logger.info(f"Reusing cached provider OpenAPI {url}")
        return cached[1]
    logger.info(f"Fetching provider OpenAPI {url}")
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(rewritten_url(url))
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


def _flattened(document: dict, schema: Any) -> dict:
    """One schema's properties and required list, with allOf branches folded in.

    A branch that adds a field states only that field, so the branches are read
    in order and the later one wins -- which is what allOf means when two
    branches describe the same field.
    """
    schema = _resolved(document, schema)
    if not isinstance(schema, dict):
        raise SchemaUnavailable("not a schema")
    properties: Dict[str, Any] = {}
    required = []
    for branch in schema.get("allOf") or []:
        folded = _flattened(document, branch)
        properties.update(folded["properties"])
        required += [name for name in folded["required"] if name not in required]
    properties.update(
        {name: _resolved(document, prop) for name, prop in (schema.get("properties") or {}).items()}
    )
    required += [name for name in schema.get("required") or [] if name not in required]
    return {**schema, "properties": properties, "required": required}


def request_schema(document: dict, pointer: str) -> dict:
    """The request fields of the operation at ``pointer``, with local refs followed.

    x-oan-request-attributes wins over requestBody where it is declared: a
    provider that seals its bodies publishes the envelope as its request body,
    and the fields a caller fills are the plaintext inside it.
    """
    operation = _resolved(document, _follow_pointer(document, pointer))
    declared = operation.get(REQUEST_ATTRIBUTES)
    if declared:
        schema = _flattened(document, declared.get("schema") or {})
    else:
        body = _resolved(document, operation.get("requestBody") or {})
        media = (body.get("content") or {}).get("application/json") or {}
        schema = _flattened(document, media.get("schema") or {})
    if not schema["properties"]:
        raise SchemaUnavailable(f"{pointer} has no JSON request fields")
    return schema


def operation_ref(resource: dict, action: str) -> str:
    """Where the catalog says this action's request contract is written."""
    for entry in (resource.get("resourceAttributes") or {}).get("actions") or []:
        if entry.get("action") == action and entry.get("operationRef"):
            return entry["operationRef"]
    raise SchemaUnavailable(f"{resource.get('id')} has no {action} operationRef")


def select_operation_ref(resource: dict) -> str:
    return operation_ref(resource, "select")
