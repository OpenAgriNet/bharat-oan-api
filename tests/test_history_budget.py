"""History retention must not depend on whether streaming reports token usage."""
import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import RequestUsage


def functions():
    root = Path(__file__).resolve().parents[1]
    namespace = dict(List=list, ModelMessage=object, json=json,
                     logger=logging.getLogger(__name__),
                     _history_encoder=lambda: SimpleNamespace(encode=lambda text, **kw: list(text)))
    for file, names in [('helpers/utils.py', {'count_tokens_for_part'}),
                        ('app/utils.py', {'group_convos', 'convo_token_usage', 'trim_history'})]:
        nodes = [n for n in ast.parse((root/file).read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), file, 'exec'), namespace)
    return namespace


def test_real_streamed_usage_does_not_erase_long_session_fields():
    f = functions()
    history = []
    for turn in range(24):
        question = 'My phone is 9876543210 and OTP is 111111' if turn == 16 else f'turn {turn}'
        history += [ModelRequest(parts=[UserPromptPart(question)]),
                    ModelResponse(parts=[TextPart('next field')],
                                  usage=RequestUsage(input_tokens=100_000, output_tokens=100))]
    kept = f['trim_history'](history, max_tokens=64_000)
    assert kept == history
    assert '9876543210' in str(kept) and '111111' in str(kept)
    used = f['convo_token_usage'](history)
    for message in history:
        if isinstance(message, ModelResponse):
            message.usage = RequestUsage()
    assert f['convo_token_usage'](history) == used


def test_large_tool_outputs_count_and_turns_stay_complete():
    f = functions()
    first = [ModelRequest(parts=[UserPromptPart('first')]), ModelResponse(parts=[TextPart('hello')])]
    middle = [ModelRequest(parts=[UserPromptPart('lookup')]),
              ModelResponse(parts=[ToolCallPart('lookup', {'query':'crop'}, tool_call_id='a')]),
              ModelRequest(parts=[ToolReturnPart('lookup', 'x'*1000, tool_call_id='a')]),
              ModelResponse(parts=[TextPart('result')])]
    newest = [ModelRequest(parts=[UserPromptPart('latest')]), ModelResponse(parts=[TextPart('answer')])]
    assert f['convo_token_usage'](middle) > 1000
    assert f['trim_history'](first+middle+newest, max_tokens=100) == first+newest


def test_empty_history_and_regional_text():
    f = functions()
    assert f['trim_history']([]) == []
    assert f['convo_token_usage']([ModelRequest(parts=[UserPromptPart('मोबाइल 9876543210')])]) > 0


def test_large_translated_system_prompt_does_not_erase_recent_history():
    f = functions()
    history = [ModelRequest(parts=[SystemPromptPart('ଓଡ଼ିଆ '*30_000), UserPromptPart('hello')]),
               ModelResponse(parts=[TextPart('hello')]),
               ModelRequest(parts=[UserPromptPart('My phone is 9876543210')]),
               ModelResponse(parts=[TextPart('OTP?')])]
    assert f['trim_history'](history, max_tokens=100) == history
