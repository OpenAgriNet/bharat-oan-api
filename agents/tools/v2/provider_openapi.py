"""A provider's native OpenAPI, read through a catalog resource's operationRef.
Shared by the v2 tools whose request follows the provider's own fields."""
import os
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FieldSpec:
    # model: the model fills it; const: fixed by the spec; adapter: the adapter fills it, so it is never sent.
    source: str
    required: bool
    types: Tuple[str, ...] = ()
    value: Any = None


def _field_source(prop: dict) -> str:
    if "const" in prop:
        return "const"
    return "adapter" if prop.get("writeOnly") else "model"


def _field_types(prop: dict) -> Tuple[str, ...]:
    declared = prop.get("type")
    return (declared,) if isinstance(declared, str) else tuple(declared or ())


def select_fields(resource: dict, schema: dict) -> Dict[str, FieldSpec]:
    """Who fills each request field. ``resource`` is where x-oan-from hints will read values from later."""
    required = set(schema.get("required") or [])
    return {
        name: FieldSpec(_field_source(prop), name in required, _field_types(prop), prop.get("const"))
        for name, prop in schema["properties"].items()
    }


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


# Transport the adapter fills, not a field a caller supplies.
_TRANSPORT_PARAMETER_LOCATIONS = ("header", "cookie")


def _parameter_fields(document: dict, operation: dict) -> dict:
    """Query and path parameters read as request fields.

    A GET states its whole input here, and so does a POST whose body is empty,
    so a reader that looks only at requestBody finds nothing to fill.
    """
    properties: Dict[str, Any] = {}
    required = []
    for entry in operation.get("parameters") or []:
        parameter = _resolved(document, entry)
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if not name or parameter.get("in") in _TRANSPORT_PARAMETER_LOCATIONS:
            continue
        schema = _resolved(document, parameter.get("schema") or {})
        prop = dict(schema) if isinstance(schema, dict) else {}
        if parameter.get("description"):
            prop["description"] = parameter["description"]
        properties[name] = prop
        if parameter.get("required") and name not in required:
            required.append(name)
    return {"properties": properties, "required": required}


def request_schema(document: dict, pointer: str) -> dict:
    """The request fields of the operation at ``pointer``, with local refs followed.

    x-oan-request-attributes wins over everything else where it is declared: a
    provider that seals its bodies publishes the envelope as its request body,
    and the fields a caller fills are the plaintext inside it.
    """
    operation = _resolved(document, _follow_pointer(document, pointer))
    declared = operation.get(REQUEST_ATTRIBUTES)
    if declared:
        return _only_if_filled(_flattened(document, declared.get("schema") or {}), pointer)

    body = _resolved(document, operation.get("requestBody") or {})
    media = (body.get("content") or {}).get("application/json") or {}
    schema = _flattened(document, media.get("schema") or {})
    parameters = _parameter_fields(document, operation)
    return _only_if_filled(
        {
            **schema,
            # A field named in both is the body's -- it carries the fuller declaration.
            "properties": {**parameters["properties"], **schema["properties"]},
            "required": parameters["required"]
            + [name for name in schema["required"] if name not in parameters["required"]],
        },
        pointer,
    )


def _only_if_filled(schema: dict, pointer: str) -> dict:
    if not schema["properties"]:
        raise SchemaUnavailable(f"{pointer} has no request fields")
    return schema


def operation_ref(resource: dict, action: str) -> str:
    """Where the catalog says this action's request contract is written."""
    for entry in (resource.get("resourceAttributes") or {}).get("actions") or []:
        if entry.get("action") == action and entry.get("operationRef"):
            return entry["operationRef"]
    raise SchemaUnavailable(f"{resource.get('id')} has no {action} operationRef")


def select_operation_ref(resource: dict) -> str:
    return operation_ref(resource, "select")


async def request_fields(resource: dict, action: str) -> Dict[str, FieldSpec]:
    """Who fills each request field for ``action``, read from the catalog's own operationRef.

    A discover result and an action in, the provider's field list out. Nothing
    here knows any provider's field names, so a provider that publishes an
    OpenAPI needs no tool change to be callable.
    """
    url, _, pointer = operation_ref(resource, action).partition("#")
    try:
        specs = select_fields(resource, request_schema(await openapi_document(url), pointer))
    except SchemaUnavailable as e:
        raise SchemaUnavailable(f"{url}: {e}") from e
    logger.info(f"{action} request fields from {url}: { {n: f.source for n, f in specs.items()} }")
    return specs
