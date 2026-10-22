"""New run-result API through the actual moderation service function."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel


def test_moderation_reads_native_usage_property():
    source = Path(__file__).resolve().parents[1] / 'app/services/chat.py'
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == '_run_moderation')
    node.decorator_list = []
    agent = Agent(FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('valid')])))
    observed = []
    async def run_agent(use_case, session, supplied_agent, prompt):
        assert supplied_agent is agent
        return await agent.run(prompt), SimpleNamespace(model_name='offline', route='test', source='test'), False
    namespace = dict(lf_update_current_observation=lambda **kw: observed.append(kw),
                     get_model_router=lambda: SimpleNamespace(run_agent=run_agent), moderation_agent=agent)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    assert asyncio.run(namespace[node.name]('crop advice', 'offline')) == 'valid'
    assert observed[-1]['output_tokens'] > 0


def test_event_stream_is_entered_as_context_manager():
    source = Path(__file__).resolve().parents[1] / 'app/services/chat.py'
    tree = ast.parse(source.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == 'run_stream_events']
    contexts = [item.context_expr for n in ast.walk(tree) if isinstance(n, ast.AsyncWith) for item in n.items]
    assert calls and all(call in contexts for call in calls)
