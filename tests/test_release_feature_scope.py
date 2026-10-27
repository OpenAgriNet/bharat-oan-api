"""Offline release checks for removed services and preserved tool availability."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents.capabilities import CAPABILITY_LOADER, load_capability, prepare_capability_tools
from agents.capability_manifest import CAPABILITIES
from agents.capability_policy import ALL_TOOLS, CapabilityState
from agents.deps import FarmerContext

ROOT = Path(__file__).resolve().parents[1]
REMOVED_TOOLS = {
    "initiate_khc_otp", "verify_khc_otp", "lookup_khc_reference", "find_khc_location",
    "get_khc_grievance_options", "check_khc_grievance_status", "register_khc_grievance",
    "imd_agromet_advisory", "get_agristack_farmer_location", "get_agristack_farmer_crops",
    "get_agristack_farmer_profile",
}
REMOVED_PATHS = (
    "agents/tools/khc.py", "agents/tools/agristack.py", "agents/tools/imd_advisory.py",
    "agents/tools/chat_place.py", "app/routers/callback.py",
    "app/routers/resolver_callback.py", "app/core/resolver_keys.py",
    "scripts/imd_agromet", "assets/prompts/capabilities/agrinet/khc",
    "assets/prompts/capabilities/agrinet/agristack",
    "assets/prompts/capabilities/agrinet/weather/agromet",
)


def test_removed_sources_routes_and_dependency_are_absent():
    for path in REMOVED_PATHS:
        assert not (ROOT / path).exists(), path
    for path in ("requirements.txt", "requirements.lock"):
        assert "psycopg2" not in (ROOT / path).read_text()
    tree = ast.parse((ROOT / "main.py").read_text())
    routers = {
        node.args[0].value.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    }
    assert not routers & {"callback", "resolver_callback"}
    assert {"chat", "health", "token", "telemetry", "image"} <= routers


def test_no_runtime_references_can_start_removed_services():
    forbidden = REMOVED_TOOLS | {
        "get_agristack_link", "agristack_status", "khc_service_note", "agromet_note",
        "callback_session_ttl_seconds", "callback_audit_db_url", "callback_audit_table_name",
    }
    for folder in ("agents", "app"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            assert not names & forbidden, (path, names & forbidden)
    assert "agristack_status" not in FarmerContext.model_fields


def test_all_retained_business_tools_remain_available():
    assert not ALL_TOOLS & REMOVED_TOOLS
    assert len(ALL_TOOLS) == 40
    assert {"initiate_kcc_otp", "check_kcc_application_status", "select_kcc_application",
            "get_mandi_prices", "weather_forecast", "search_advisory", "search_documents",
            "search_pests_diseases", "pmkisan_submit_grievance", "pmfby_submit_grievance",
            "check_aif_grievance_status"} <= ALL_TOOLS
    assert CAPABILITIES["weather"] == {"weather_forecast"}


@pytest.mark.parametrize("language", ["as", "bn", "en", "gu", "hi", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te"])
def test_rendered_prompts_do_not_offer_removed_features(language):
    env = Environment(loader=FileSystemLoader(ROOT / "assets/prompts"))
    prompt = env.get_template(f"agrinet_{language}.md").render().lower()
    for name in ("khc", "agristack", "agromet", *REMOVED_TOOLS):
        assert name not in prompt
    for tool in ("initiate_kcc_otp", "get_mandi_prices", "weather_forecast", "search_advisory",
                 "pmkisan_submit_grievance", "pmfby_submit_grievance"):
        assert tool in prompt


@pytest.mark.parametrize("group,tool", [
    ("khc", "check_khc_grievance_status"),
    ("agristack", "get_agristack_farmer_profile"),
    ("weather", "imd_agromet_advisory"),
])
def test_saved_history_cannot_restore_removed_tools(group, tool):
    state = CapabilityState()
    state.restore([ModelRequest(parts=[ToolReturnPart(tool_name="load_capability", content={
        "capability": group, "loaded_tools": [tool],
    })])])
    assert tool not in state.visible
    ctx = SimpleNamespace(deps=SimpleNamespace(capabilities=state))
    if group == "weather":
        assert asyncio.run(load_capability(ctx, group))["loaded_tools"] == ["weather_forecast"]
    else:
        with pytest.raises(ModelRetry):
            asyncio.run(load_capability(ctx, group))


@pytest.mark.parametrize("tool", sorted(REMOVED_TOOLS))
def test_explicit_model_request_cannot_call_removed_tool(tool):
    def retained_tool() -> str:
        raise AssertionError("An unrelated service must not be called")

    step = 0

    def model(messages, info):
        nonlocal step
        assert not {definition.name for definition in info.function_tools} & REMOVED_TOOLS
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart(tool, {})])
        return ModelResponse(parts=[TextPart("This service is not available here.")])

    agent = Agent(
        FunctionModel(model), deps_type=FarmerContext,
        tools=[Tool(retained_tool, name="weather_forecast"), CAPABILITY_LOADER],
        prepare_tools=prepare_capability_tools,
    )
    result = agent.run_sync(f"Please use {tool}", deps=FarmerContext(query="test", session_id="offline"))
    assert result.output == "This service is not available here."
