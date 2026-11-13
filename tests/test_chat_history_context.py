"""Current state is transient; conversation and tool evidence stay in history."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
from fastapi import BackgroundTasks
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart, ImageUrl
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage
from pydantic_core import to_jsonable_python

from app import utils
from tests.test_ffs import MemoryCache


def old_prompt(query, stage):
    return f'**User:** "{query}"\n**Selected Language:** English\n**FFS workflow:** {{"stage": "{stage}"}}'


def test_cleaning_preserves_real_user_text_images_and_tool_pairs():
    current = old_prompt('Keep this literal **FFS workflow:** example\n\n---\n\nand my question', 'old-state')
    prefix = '**Conversation**\n\nold copied answer\n\n---\n\n'
    image = ImageUrl(url='https://example.invalid/plot.jpg')
    history = [ModelRequest(parts=[UserPromptPart([prefix + current, image])]),
               ModelResponse(parts=[ToolCallPart('lookup', {'plot': 'land-1'}, tool_call_id='one')]),
               ModelRequest(parts=[ToolReturnPart('lookup', {'bags': 6}, tool_call_id='one')]),
               ModelResponse(parts=[TextPart('Your allowance is six bags.')])]
    original = deepcopy(history)
    clean = utils.clean_chat_history(history)
    assert history == original
    content = clean[0].parts[0].content
    assert content[1] == image
    assert 'old copied answer' not in content[0] and 'old-state' not in content[0]
    assert 'literal **FFS workflow:** example\n\n---\n\nand my question' in content[0]
    assert clean[1:] == original[1:]
    assert utils.clean_chat_history(clean) == clean


def test_cache_load_migrates_old_snapshots_and_save_does_not_store_new_ones(monkeypatch):
    memory = MemoryCache()
    monkeypatch.setattr(utils, 'cache', memory)
    async def run():
        history = [ModelRequest(parts=[UserPromptPart(old_prompt('Old question', 'old-state'))]),
                   ModelResponse(parts=[TextPart('Old answer')])]
        key = f'session_{utils.HISTORY_SUFFIX}'
        await memory.set(key, to_jsonable_python(history))
        restored = await utils._get_message_history('session')
        assert 'old-state' not in restored[0].parts[0].content
        history.append(ModelRequest(parts=[UserPromptPart(old_prompt('New question', 'new-state'))]))
        await utils.update_message_history('session', history)
        saved = json.dumps(await memory.get(key))
        assert '**FFS workflow:**' not in saved
        assert 'Old question' in saved and 'Old answer' in saved and 'New question' in saved
    asyncio.run(run())


def test_trimming_keeps_tool_pairs_without_reviving_first_turn_snapshot():
    from agents.deps import FarmerContext
    history = []
    for index in range(3):
        history.extend([ModelRequest(parts=[UserPromptPart(old_prompt(f'turn {index}', f'old-{index}'))]),
                        ModelResponse(parts=[ToolCallPart('lookup', {}, tool_call_id=f'call-{index}')]),
                        ModelRequest(parts=[ToolReturnPart('lookup', {'result': index}, tool_call_id=f'call-{index}')]),
                        ModelResponse(parts=[TextPart(f'answer {index}')], usage=RequestUsage(input_tokens=10))])
    trimmed = utils.trim_history(utils.clean_chat_history(history), max_tokens=20)
    serialized = json.dumps(to_jsonable_python(trimmed))
    assert '**FFS workflow:**' not in serialized
    assert 'turn 0' in serialized and 'turn 2' in serialized and 'turn 1' not in serialized
    calls = [p.tool_call_id for m in trimmed for p in m.parts if p.part_kind == 'tool-call']
    returns = [p.tool_call_id for m in trimmed for p in m.parts if p.part_kind == 'tool-return']
    assert calls == returns == ['call-0', 'call-2']
    prompt = FarmerContext(session_id='trim-test', query='continue', ffs_context={'stage': 'current'}).get_user_message()
    assert (serialized + prompt).count('**FFS workflow:**') == 1


async def exercise_chat_history(monkeypatch, model):
    """Real chat service/serializer/cache; isolated model, moderation and telemetry."""
    from app.services import chat
    from app.services.model_routing import ModelRouteDecision
    memory = MemoryCache()
    monkeypatch.setattr(utils, 'cache', memory)
    moderation_inputs = []
    async def noop(*args, **kwargs):
        return None
    async def moderation(prompt, session):
        moderation_inputs.append(prompt)
        return SimpleNamespace(category='Valid Agricultural')
    decision = ModelRouteDecision('test', 'test', 'test')
    async def resolve(*args, **kwargs):
        return decision
    async def route(use_case, session, attempt, **kwargs):
        return await attempt(decision), decision, False
    monkeypatch.setattr(chat, 'get_model_router', lambda: SimpleNamespace(resolve=resolve, run=route))
    monkeypatch.setattr(chat, 'get_registry', lambda: SimpleNamespace(
        get_default_alias=lambda _: 'test', get_model_name=lambda _: 'test',
        get_model=lambda _: model, get_kind=lambda _: 'openai'))
    for name in ('get_agristack_link', '_record_chat_turn', '_flush_langfuse'):
        monkeypatch.setattr(chat, name, noop)
    monkeypatch.setattr(chat, '_queue_telemetry_event', lambda *args: None)
    monkeypatch.setattr(chat, '_run_moderation', moderation)
    agent = Agent(model, system_prompt='Answer briefly. If asked to check the mock allowance, call lookup_allowance once.')
    @agent.tool_plain
    async def lookup_allowance() -> dict:
        return {'bags': 6, 'product': 'urea'}
    monkeypatch.setattr(chat, 'agrinet_agent', agent)
    history = [ModelRequest(parts=[UserPromptPart(old_prompt('I want maize for Shirur.', 'obsolete-state'))]),
               ModelResponse(parts=[TextPart('We will use Shirur.')]),
               ModelRequest(parts=[UserPromptPart('**Conversation**\n\n' + old_prompt('I want maize for Shirur.', 'obsolete-state') +
                   '\n\n---\n\n' + old_prompt('One bag of urea only.', 'also-obsolete'))]),
               ModelResponse(parts=[TextPart('One bag of urea selected.')])]
    outputs = []
    for turn, query in enumerate(['Check the mock allowance.', 'Which product and quantity did I ask to buy?'], 1):
        parts = [part async for part in chat.stream_chat_messages(
            query, 'history-audit', 'en', 'en', 'test-user', history, BackgroundTasks(),
            qid=f'turn-{turn}', ffs_context={'stage': f'current-state-{turn}', 'order': [{'product': 'urea', 'bags': 1}]})]
        outputs.append(''.join(parts))
        history = await utils._get_message_history('history-audit')
        assert '**FFS workflow:**' not in json.dumps(to_jsonable_python(history))
        assert '**Conversation**' not in json.dumps(to_jsonable_python(history))
    assert 'I want maize for Shirur.' in moderation_inputs[0]
    assert 'obsolete-state' not in moderation_inputs[0]
    return outputs, history


def test_model_wire_receives_one_current_snapshot_and_native_tool_history(monkeypatch):
    requests = []
    async def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        text = json.dumps(body['messages'])
        assert text.count('**FFS workflow:**') == 1
        assert 'obsolete-state' not in text and 'also-obsolete' not in text
        assert '**Conversation**' not in text
        assert text.count('I want maize for Shirur.') == 1
        if len(requests) == 1:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'lookup-1', 'type': 'function',
                'function': {'name': 'lookup_allowance', 'arguments': '{}'}}]}
            finish = 'tool_calls'
        else:
            delta = {'role': 'assistant', 'content': 'You requested one bag of urea.'}
            finish = 'stop'
        if len(requests) == 3:
            assert 'current-state-1' not in text and 'current-state-2' in text
            assert any(m['role'] == 'tool' and '6' in m['content'] for m in body['messages'])
            assert any(m.get('tool_calls') for m in body['messages'])
        chunks = [{'id': 'chatcmpl-test', 'created': 1, 'model': 'test', 'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': d, 'finish_reason': f}]} for d, f in [(delta, None), ({}, finish)]]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = AsyncOpenAI(api_key='test', base_url='http://unused.invalid/v1', http_client=http, max_retries=0)
            model = OpenAIChatModel('test', provider=OpenAIProvider(openai_client=client))
            outputs, history = await exercise_chat_history(monkeypatch, model)
            assert len(requests) == 3
            assert all(outputs)
            assert len([p for m in history for p in m.parts if p.part_kind == 'user-prompt']) == 4
    asyncio.run(asyncio.wait_for(run(), timeout=15))
