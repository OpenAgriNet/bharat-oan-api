"""A provider's native OpenAPI, read through a catalog resource's operationRef.
Shared by the v2 tools whose request follows the provider's own fields."""
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

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

# The JSON type the Beckn layer carries, where it differs from the type the
# provider wants on the wire. The adapter binds a session to a string and
# ignores anything else, so a numeric key has to travel as text and be cast in
# the mapping. Without this the two needs cannot both be stated.
CARRIED_AS = "x-oan-as"


class From:
    """The names the contracts use in x-oan-from.

    One vocabulary across every provider: a mobile is a mobile whether KHC or
    PMFBY asks for it, and the contract says which of its own fields that
    mobile belongs in. A tool offers what it holds by these names and never
    writes a provider's field name.
    """

    MOBILE = "subject.mobile"
    NAME = "subject.name"
    GENDER = "subject.gender"
    LANGUAGE = "subject.language"
    STATE = "subject.state"
    DISTRICT = "subject.district"
    VILLAGE = "subject.village"
    SUBJECT_ID = "subject.id"
    LOOKUP = "subject.lookup"
    BENEFICIARY = "beneficiary.id"
    ENROLMENT = "enrolment.id"
    OTP = "challenge.otp"
    SESSION = "session"
    APPLICATION = "application.id"
    GRIEVANCE_ID = "grievance.id"
    GRIEVANCE_TEXT = "grievance.text"
    CATEGORY = "grievance.category"
    SUB_CATEGORY = "grievance.subCategory"
    REFERENCE = "grievance.reference"
    FILED_ON = "grievance.filedOn"
    SOURCE = "grievance.source"
    SCHEME = "scheme.id"
    DIVISION = "scheme.division"
    SEASON = "season"
    YEAR = "year"
    POLICY = "policy.id"
    CROP_CODE = "crop.code"
    CROP_IDS = "crop.ids"
    CYCLE = "report.cycle"
    SOIL_RESULTS = "soil.results"
    NATURAL_FARMING = "farming.natural"


class SchemaUnavailable(Exception):
    """The operation's request schema could not be fetched or read."""


@dataclass(frozen=True)
class FieldSpec:
    # model: the model fills it; const: fixed by the spec; adapter: the adapter fills it, so it is never sent.
    source: str
    required: bool
    types: Tuple[str, ...] = ()
    value: Any = None
    # What value belongs here, named by the contract's x-oan-from. "" when it says nothing.
    hint: str = ""
    # What to send when nothing held fills it, where the contract states one.
    default: Any = None
    # The type to send it as, when the Beckn layer carries it differently.
    carried: str = ""
    # The fields inside this one, for a field the provider declares as an object.
    fields: Mapping[str, "FieldSpec"] = field(default_factory=dict)


def _field_source(prop: dict) -> str:
    if "const" in prop:
        return "const"
    return "adapter" if prop.get("writeOnly") else "model"


def _field_types(prop: dict) -> Tuple[str, ...]:
    declared = prop.get("type")
    return (declared,) if isinstance(declared, str) else tuple(declared or ())


def _spec(prop: dict, required: bool) -> FieldSpec:
    """One field read as who fills it, what it holds, and what is inside it.

    An object field is read through the same way, so a provider that groups its
    input -- a GraphQL variables block, say -- is filled from hints exactly like
    a flat body.
    """
    inner = prop.get("properties") or {}
    inner_required = set(prop.get("required") or [])
    return FieldSpec(
        _field_source(prop),
        required,
        _field_types(prop),
        prop.get("const"),
        prop.get(VALUE_SOURCE) or "",
        prop.get("default"),
        prop.get(CARRIED_AS) or "",
        {name: _spec(nested, name in inner_required) for name, nested in inner.items()},
    )


def select_fields(resource: dict, schema: dict) -> Dict[str, FieldSpec]:
    """Who fills each request field. ``resource`` is where x-oan-from hints will read values from later."""
    required = set(schema.get("required") or [])
    return {name: _spec(prop, name in required) for name, prop in schema["properties"].items()}


def as_declared(value: Any, types: Tuple[str, ...]) -> Any:
    """``value`` in the JSON type the field is declared as.

    KCC's OTP is the reason this exists: the contract says integer and the quoted
    form is refused, and the farmer reads the code out as text.
    """
    try:
        if "integer" in types:
            return int(value)
        if "number" in types:
            return float(value)
    except (TypeError, ValueError):
        logger.warning(f"Could not read {value!r} as {types}; sending it as given")
        return value
    return str(value) if "string" in types else value


def filled_fields(specs: Mapping[str, FieldSpec], held: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The request fields to send, and the required ones nothing held can fill.

    ``held`` is keyed by x-oan-from name, so a caller offers the values it has --
    a mobile, an OTP, a session -- and the contract decides which of the
    provider's own fields each one lands in. No field name is known here.
    """
    values: Dict[str, Any] = {}
    unfillable: List[str] = []
    for name, spec in specs.items():
        if spec.source == "const":
            values[name] = spec.value
            continue
        if spec.source == "adapter":
            continue
        value = held.get(spec.hint) if spec.hint else None
        # A hint on the field itself wins over the fields inside it: a provider
        # that asks for a whole block -- a soil result, say -- is handed the one
        # value the caller holds, not a block assembled field by field.
        if spec.fields and value in (None, ""):
            inside, missing = filled_fields(spec.fields, held)
            if inside:
                values[name] = inside
            unfillable += [f"{name}.{one}" for one in missing]
            if spec.required and not inside:
                unfillable.append(f"{name} (nothing held fills any of it)")
            continue
        if value in (None, ""):
            value = spec.default
        if value in (None, ""):
            if spec.required:
                unfillable.append(f"{name} (wants {spec.hint or 'no ' + VALUE_SOURCE + ' hint'})")
            continue
        values[name] = as_declared(value, (spec.carried,) if spec.carried else spec.types)
    return values, unfillable


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
        target = _follow_pointer(document, ref[1:])
        # OpenAPI 3.1 lets a $ref carry siblings, and the siblings win. A field
        # that reuses a named type and adds its own x-oan-from reads that way.
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        node = {**target, **siblings} if siblings and isinstance(target, dict) else target
    return node


def _flattened(document: dict, schema: Any) -> dict:
    """One schema's properties and required list, with composed branches folded in.

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
    variants = [_flattened(document, branch) for branch in schema.get("anyOf") or schema.get("oneOf") or []]
    for folded in variants:
        properties.update(folded["properties"])
    # A field only one variant asks for is optional overall: it belongs to the call
    # that variant describes, and the caller may be making the other one.
    required += [name for name in (variants[0]["required"] if variants else [])
                 if name not in required and all(name in other["required"] for other in variants[1:])]
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
        # These belong to the parameter, not to the type it reuses: two
        # parameters can share one named schema and still want different values.
        prop.update({key: value for key, value in parameter.items() if key.startswith("x-oan-")})
        properties[name] = prop
        if parameter.get("required") and name not in required:
            required.append(name)
    return {"properties": properties, "required": required}


def _variant_schemas(document: dict, schema: Any) -> List[dict]:
    """One flattened schema per shape the operation declares.

    A provider that accepts either of two bodies -- AIF's one endpoint that
    both sends and answers an OTP, PM-KISAN's lookup by registration number or
    by mobile -- says so with anyOf or oneOf. Merging those into one field list
    loses which fields go together, so each shape is kept whole and the caller
    sends the one it can fill.
    """
    schema = _resolved(document, schema)
    if not isinstance(schema, dict):
        raise SchemaUnavailable("not a schema")
    branches = schema.get("anyOf") or schema.get("oneOf")
    if not branches:
        return [_flattened(document, schema)]
    outer = {key: value for key, value in schema.items() if key not in ("anyOf", "oneOf")}
    return [
        _flattened(document, {**outer, "allOf": [*(outer.get("allOf") or []), one]})
        for branch in branches
        for one in _variant_schemas(document, branch)
    ]


def request_schemas(document: dict, pointer: str) -> List[dict]:
    """Every shape the operation at ``pointer`` accepts, with local refs followed.

    x-oan-request-attributes stands in for the request body where it is
    declared: a provider that seals its bodies publishes the envelope as its
    body, and the fields a caller fills are the plaintext inside it. It also
    names attributes the Beckn call carries but the provider never sees -- the
    mobile a session was bought for, say. Query and path parameters are real
    fields either way, so they are read in both cases.
    """
    operation = _resolved(document, _follow_pointer(document, pointer))
    declared = operation.get(REQUEST_ATTRIBUTES)
    if declared:
        body_schema = declared.get("schema") or {}
    else:
        body = _resolved(document, operation.get("requestBody") or {})
        body_schema = ((body.get("content") or {}).get("application/json") or {}).get("schema") or {}
    parameters = _parameter_fields(document, operation)
    return [
        _only_if_filled(
            {
                **schema,
                # A field named in both is the body's -- it carries the fuller declaration.
                "properties": {**parameters["properties"], **schema["properties"]},
                "required": parameters["required"]
                + [name for name in schema["required"] if name not in parameters["required"]],
            },
            pointer,
        )
        for schema in _variant_schemas(document, body_schema)
    ]


def request_schema(document: dict, pointer: str) -> dict:
    """Every request field of the operation at ``pointer``, the shapes folded together.

    A field only one shape asks for is optional here: it belongs to the call
    that shape describes, and the caller may be making the other one.
    """
    schemas = request_schemas(document, pointer)
    properties: Dict[str, Any] = {}
    for schema in schemas:
        properties.update(schema["properties"])
    return {
        **schemas[0],
        "properties": properties,
        "required": [
            name for name in schemas[0]["required"]
            if all(name in other["required"] for other in schemas[1:])
        ],
    }


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


async def _request_schemas(resource: dict, action: str) -> Tuple[str, List[dict]]:
    url, _, pointer = operation_ref(resource, action).partition("#")
    try:
        return url, request_schemas(await openapi_document(url), pointer)
    except SchemaUnavailable as e:
        raise SchemaUnavailable(f"{url}: {e}") from e


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


async def request_variants(resource: dict, action: str) -> List[Dict[str, FieldSpec]]:
    """Each shape ``action`` accepts, as the fields that make it up."""
    url, schemas = await _request_schemas(resource, action)
    variants = [select_fields(resource, schema) for schema in schemas]
    logger.info(f"{action} request shapes from {url}: {[sorted(v) for v in variants]}")
    return variants


def _held_count(specs: Mapping[str, FieldSpec], values: Mapping[str, Any]) -> int:
    """How many of this shape's fields a held value actually filled."""
    hinted = {spec.hint for spec in specs.values() if spec.hint}
    return len(hinted & {hint for hint, value in values.items() if value not in (None, "")})


def filled_request(
    variants: List[Dict[str, FieldSpec]], held: Mapping[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """The body to send, chosen from the shapes the provider declares.

    Of the shapes everything is held for, the one that uses the most of what is
    held: holding an OTP means the call that answers it, not the one that asks
    for it again. When none can be filled, the nearest miss is reported so the
    log names a field rather than a shape.
    """
    best: Optional[Tuple[Dict[str, Any], List[str]]] = None
    fillable: List[Tuple[int, Dict[str, Any]]] = []
    for specs in variants:
        values, unfillable = filled_fields(specs, held)
        if not unfillable:
            fillable.append((_held_count(specs, held), values))
        elif best is None or len(unfillable) < len(best[1]):
            best = (values, unfillable)
    if fillable:
        return max(fillable, key=lambda pair: pair[0])[1], []
    return best if best else ({}, ["no shape declared"])


async def filled_for(resource: dict, action: str, held: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """``action``'s request body built from what the caller holds, and what it could not fill."""
    return filled_request(await request_variants(resource, action), held)
