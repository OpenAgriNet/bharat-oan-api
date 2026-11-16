"""FFS schema guidance must render without changing runtime validation."""
import asyncio
from copy import deepcopy
import json

import httpx
from openai import AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
import pytest

from agents.tools import TOOLS, _gemma_schema
from helpers.gemma_schema import gemma_compatible


FFS_TOOLS = [tool for tool in TOOLS if tool.name.startswith("ffs_")]


def assert_flat(schema):
    if isinstance(schema, dict):
        assert not {"anyOf", "$ref", "$defs"}.intersection(schema)
        for value in schema.values():
            assert_flat(value)
    elif isinstance(schema, list):
        for value in schema:
            assert_flat(value)


def test_nested_optional_schema_preserves_meaning_and_original():
    schema = {
        "$defs": {"Item": {"type": "object", "required": ["bags"],
                           "properties": {"bags": {"type": "integer", "minimum": 1}}}},
        "type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["initial", "more"]},
            "order": {"anyOf": [{"type": "array", "items": {"$ref": "#/$defs/Item"}},
                                 {"type": "null"}], "default": None, "description": "Farmer's order"},
            "another": {"$ref": "#/$defs/Item"},
        },
    }
    original = deepcopy(schema)
    fixed = gemma_compatible(schema)
    assert schema == original
    assert_flat(fixed)
    assert fixed["required"] == ["action"]
    assert fixed["properties"]["action"] == schema["properties"]["action"]
    assert fixed["properties"]["order"] == {
        "type": "array", "items": schema["$defs"]["Item"], "nullable": True,
        "default": None, "description": "Farmer's order",
    }
    assert fixed["properties"]["another"] == schema["$defs"]["Item"]


@pytest.mark.parametrize("schema", [
    {"anyOf": [{"type": "string"}, {"type": "integer"}]},
    {"$defs": {"Node": {"$ref": "#/$defs/Node"}}, "$ref": "#/$defs/Node"},
    {"$ref": "https://example.invalid/schema"},
    {"$ref": "#/$defs/Missing"},
    {"oneOf": [{"type": "string"}, {"type": "integer"}]},
])
def test_unsupported_shapes_fail_explicitly(schema):
    with pytest.raises(ValueError):
        gemma_compatible(schema)


def test_all_five_registered_tools_prepare_without_mutating_validator_schema():
    async def run():
        assert len(FFS_TOOLS) == 5
        for tool in TOOLS:
            assert (tool.prepare is _gemma_schema) == tool.name.startswith("ffs_")
        for tool in FFS_TOOLS:
            original = deepcopy(tool.tool_def.parameters_json_schema)
            prepared = await tool.prepare_tool_def(None)
            assert prepared is not tool.tool_def
            assert_flat(prepared.parameters_json_schema)
            assert tool.tool_def.parameters_json_schema == original
            assert prepared.name == tool.name
            assert prepared.description == tool.tool_def.description
        shops = next(t for t in FFS_TOOLS if t.name == "ffs_find_shops")
        prepared = await shops.prepare_tool_def(None)
        order = prepared.parameters_json_schema["properties"]["order"]
        assert order["type"] == "array" and order["nullable"]
        assert order["items"]["properties"]["bags"]["type"] == "integer"
        assert {"product", "bags"} <= set(order["items"]["required"])
        # Original validation, including our numeric-string compatibility, survives.
        validator = shops.function_schema.validator
        valid = validator.validate_python({"order": [{"product": "urea", "bags": "1"}]})
        assert valid["order"][0].bags == 1
        for invalid in ({"products": []}, [{"product": "urea"}],
                        [{"product": "urea", "bags": True}], [{"product": "urea", "bags": 1.5}]):
            with pytest.raises(ValidationError):
                validator.validate_python({"order": invalid})
    asyncio.run(run())


def test_non_gemma_chat_client_sends_prepared_schemas_offline():
    """Exercise actual SDK serialization, not live provider schema acceptance."""
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion",
            "created": 1, "model": "gpt-4o", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "Ready"}, "finish_reason": "stop"}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AsyncOpenAI(api_key="test", base_url="http://unused.invalid/v1", http_client=http)
            model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(openai_client=client))
            agent = Agent(model, tools=FFS_TOOLS)
            output = await agent.run("List no tools; say Ready.")
            assert output.output == "Ready"
        assert len(requests) == 1
        assert len(requests[0]["tools"]) == 5
        for tool in requests[0]["tools"]:
            assert_flat(tool["function"]["parameters"])
    asyncio.run(run())
