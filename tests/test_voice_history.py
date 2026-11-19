"""Ported from voice-oan-api tests/test_ffs_voice_history.py."""
from copy import deepcopy
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart
from app.voice.history import clean_voice_history, group_voice_convos, trim_voice_history


def test_legacy_snapshots_are_removed_without_mutating_or_losing_tool_pairs():
    history = [
        ModelRequest(parts=[UserPromptPart('**User:** "one bag"\n**FFS workflow:** {"stage":"shops_ready"}')]),
        ModelResponse(parts=[ToolCallPart('ffs_find_shops', {'order': []}, tool_call_id='shop')]),
        ModelRequest(parts=[ToolReturnPart('ffs_find_shops', {
            'status':'ok','data':{'shops':[{'shop_ref':'retailer-4','name':'Example','stock':{'urea':777},'territory':'V'}],
                                'backend_exchanges':[{'body':'huge private response'}]}},tool_call_id='shop')]),
        ModelResponse(parts=[TextPart('Which shop?')]),
    ]
    original=deepcopy(history)
    clean=clean_voice_history(history)
    assert history == original
    assert len(clean)==len(history)
    assert clean[0].parts[0].content=='**User:** "one bag"'
    assert clean[1] == history[1] and clean[3] == history[3]
    assert clean[2].parts[0].tool_call_id=='shop'
    assert 'huge private' not in str(clean) and "'stock'" not in str(clean)
    assert clean_voice_history(clean)==clean


def test_user_text_that_mentions_snapshot_marker_is_preserved():
    text='**User:** "He wrote \\\"yes\\\"\n**FFS workflow:** this is my text"'
    history=[ModelRequest(parts=[UserPromptPart(text)])]
    assert clean_voice_history(history)==history


def test_non_ffs_history_is_unchanged_including_incomplete_replies():
    history=[ModelRequest(parts=[UserPromptPart('I want to know.')]),
             ModelResponse(parts=[TextPart('What would you like to know?')]),
             ModelRequest(parts=[UserPromptPart('I want to know how to apply to PM Kisan.')])]
    assert clean_voice_history(history)==history


def test_trim_always_keeps_the_welcome_exchange():
    welcome = [ModelRequest(parts=[UserPromptPart('Hello')]), ModelResponse(parts=[TextPart('Welcome to Bharati')])]
    turns = []
    for i in range(5):
        turns += [ModelRequest(parts=[UserPromptPart(f'question {i} ' + 'x' * 400)]),
                  ModelResponse(parts=[TextPart(f'answer {i} ' + 'y' * 400)])]
    trimmed = trim_voice_history(welcome + turns, max_tokens=600)
    assert trimmed[:2] == welcome
    assert trimmed[-1] == turns[-1]
    assert len(trimmed) < len(welcome + turns)
    assert len(group_voice_convos(welcome + turns)) == 6
