import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, RetryPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart

from helpers.ask_first import farmer_was_asked
from agents.tools import search as search_tools


def U(text):
    return ModelRequest(parts=[UserPromptPart(content=text)])


def A(text):
    return ModelResponse(parts=[TextPart(content=text)])


def CALL(tool="search_pests_diseases", text=""):
    parts = ([TextPart(content=text)] if text else []) + [ToolCallPart(tool_name=tool, args={})]
    return ModelResponse(parts=parts)


def RET(tool="search_pests_diseases", content="advice"):
    return ModelRequest(parts=[ToolReturnPart(tool_name=tool, tool_call_id="c", content=content)])


def ctx(messages):
    return SimpleNamespace(messages=messages)


def test_first_question_is_not_asked_yet():
    assert not farmer_was_asked(ctx([U("aphids in wheat"), CALL(text="Let me look.")]))


def test_question_ask_reply_is_asked():
    assert farmer_was_asked(ctx([U("aphids in wheat"), A("Generic or personalised?"), U("generic"), CALL()]))


def test_answer_then_late_ask_does_not_count():
    # the pests tool answered q1 and "generic or personalised?" came after the answer
    msgs = [U("aphids"), CALL(), RET(), A("Advice... generic or personalised?"), U("harvest time?"), CALL()]
    assert not farmer_was_asked(ctx(msgs))


def test_follow_up_after_an_answer_needs_a_new_ask():
    msgs = [U("q1"), A("ask"), U("generic"), CALL("search_advisory"), RET("search_advisory"), A("answer"), U("q2"), CALL()]
    assert not farmer_was_asked(ctx(msgs))
    # the refused call comes back as a retry prompt, not as a result
    msgs += [ModelRequest(parts=[RetryPromptPart(content="ask first", tool_name="search_pests_diseases")]), A("generic or personalised?"), U("personalised"), CALL()]
    assert farmer_was_asked(ctx(msgs))


def test_other_tools_before_the_ask_do_not_break_it():
    msgs = [U("aphids"), CALL("search_terms"), RET("search_terms"), A("generic or personalised?"), U("generic"), CALL()]
    assert farmer_was_asked(ctx(msgs))


def test_later_tool_calls_in_the_same_run_keep_the_answer():
    msgs = [U("aphids"), A("generic or personalised?"), U("generic"), CALL("search_advisory"), RET("search_advisory"), CALL()]
    assert farmer_was_asked(ctx(msgs))


def test_without_messages_the_check_does_not_block():
    assert farmer_was_asked(SimpleNamespace())


def test_pests_tool_refuses_before_the_ask():
    c = SimpleNamespace(messages=[U("aphids in wheat"), CALL(text="Looking.")], deps=SimpleNamespace())
    with pytest.raises(ModelRetry, match="generic or personalised"):
        asyncio.run(search_tools.search_pests_diseases(c, "aphids wheat"))


def test_mentions_crop():
    from helpers.ask_first import mentions_crop

    assert mentions_crop("wheat lodging prevention")
    assert mentions_crop("Paddy brown planthopper")
    assert not mentions_crop("cattle foot and mouth disease")
    assert not mentions_crop("buffalo vaccination schedule")
    assert mentions_crop("rice")


def test_documents_tool_asks_first_for_crop_questions_only():
    c = SimpleNamespace(messages=[U("wheat lodging"), CALL("search_documents", text="Looking.")], deps=SimpleNamespace())
    with pytest.raises(ModelRetry, match="generic or personalised"):
        asyncio.run(search_tools.search_documents(c, "wheat lodging prevention"))


def test_rounds_count_each_asked_and_answered_question():
    from helpers.ask_first import asked_rounds

    one = [U("q"), A("generic or personalised?"), U("personalised"), CALL("search_advisory")]
    assert asked_rounds(ctx(one)) == 1
    two = [U("q"), A("generic or personalised?"), U("personalised"), A("district, days, season?"), U("Rabi, Pune, 40"), CALL()]
    assert asked_rounds(ctx(two)) == 2
    assert asked_rounds(ctx([U("q"), CALL()])) == 0


def test_rounds_start_again_after_an_answer():
    from helpers.ask_first import asked_rounds

    msgs = [U("q"), A("ask"), U("generic"), CALL("search_advisory"), RET("search_advisory"), A("answer"), U("q2"), CALL()]
    assert asked_rounds(ctx(msgs)) == 0
