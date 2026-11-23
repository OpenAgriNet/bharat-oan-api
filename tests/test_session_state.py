"""Session state store and the model-history composer (no Redis, no model)."""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from pydantic_ai.messages import (ModelRequest, ModelResponse, SystemPromptPart, TextPart, ToolCallPart,
                                  ToolReturnPart, UserPromptPart)

from agents.deps import FarmerContext
from app import utils
from app.services import session_state


class FakeCache:
    def __init__(self):
        self.values = {}

    async def get(self, key, namespace=None):
        return self.values.get((namespace, key))

    async def set(self, key, value, ttl=None, namespace=None):
        self.values[(namespace, key)] = json.loads(json.dumps(value))

    async def delete(self, key, namespace=None):
        self.values.pop((namespace, key), None)


def deps(session_id="s1", **state):
    return FarmerContext(session_id=session_id, query="q", session_state=state)


def test_remember_merges_removes_and_marks_changed():
    d = deps()
    ctx = SimpleNamespace(deps=d)
    session_state.remember(ctx, "kcc", mobile="9876543210", otp_step="sent")
    session_state.remember(ctx, "kcc", otp_step="verified", mobile=None)
    assert d.session_state == {"kcc": {"otp_step": "verified"}}
    assert d.session_state_changed == {"kcc"}


def test_remember_same_values_is_not_a_change():
    d = deps(kcc={"mobile": "1"})
    session_state.remember(d, "kcc", mobile="1")
    assert d.session_state_changed == set()


def test_forget_drops_the_section():
    d = deps(aif={"beneficiary_id": "1"})
    session_state.forget(d, "aif")
    assert d.session_state == {} and d.session_state_changed == {"aif"}


def test_save_and_load_round_trip_per_section():
    session_state.register_section("test_a")
    session_state.register_section("test_b")
    fake = FakeCache()
    with mock.patch.object(session_state, "cache", fake):
        d = deps("round-trip")
        session_state.remember(d, "test_a", x=1)
        session_state.remember(d, "test_b", y="two")
        asyncio.run(session_state.save_changed_state(d))
        assert d.session_state_changed == set()
        loaded = asyncio.run(session_state.load_state("round-trip"))
        assert loaded["test_a"] == {"x": 1} and loaded["test_b"] == {"y": "two"}
        # The key is a hash of the session ID, never the ID itself.
        assert all("round-trip" not in key for _, key in fake.values)
        session_state.forget(d, "test_a")
        asyncio.run(session_state.save_changed_state(d))
        assert "test_a" not in asyncio.run(session_state.load_state("round-trip"))
        assert asyncio.run(session_state.load_state("another-session")).get("test_b") is None


def test_render_uses_views_and_hides_internal_sections():
    session_state.register_section("test_hidden", view=lambda state: None)
    session_state.register_section("test_view", view=lambda state: {"stage": state["stage"]})
    text = session_state.render_state({"test_hidden": {"mentioned": True},
                                       "test_view": {"stage": "otp", "token": "secret"},
                                       "kcc": {"mobile": "1"}})
    assert json.loads(text) == {"test_view": {"stage": "otp"}, "kcc": {"mobile": "1"}}
    assert session_state.render_state({"test_hidden": {"mentioned": True}}) == ""


def test_user_message_has_one_state_line_only_when_there_is_state():
    assert "Session state" not in deps().get_user_message()
    message = deps(kcc={"mobile": "1"}).get_user_message()
    assert message.count(session_state.SESSION_STATE_LABEL) == 1
    assert '{"kcc":{"mobile":"1"}}' in message


def test_saved_history_strips_the_state_line():
    message = deps(kcc={"mobile": "1"}).get_user_message()
    cleaned = utils.clean_chat_history([ModelRequest(parts=[UserPromptPart(message)])])
    assert "Session state" not in cleaned[0].parts[0].content


def _history(turns=3):
    history = []
    for i in range(turns):
        request_parts = [UserPromptPart(f"question {i}")]
        if i == 0:
            request_parts.insert(0, SystemPromptPart("system"))
        history += [ModelRequest(parts=request_parts),
                    ModelResponse(parts=[TextPart(f"checking {i}"), ToolCallPart("lookup", {}, tool_call_id=f"c{i}")]),
                    ModelRequest(parts=[ToolReturnPart("lookup", f"result {i}", tool_call_id=f"c{i}")]),
                    ModelResponse(parts=[TextPart(f"answer {i}")])]
    return history


def _kinds(messages):
    return [p.part_kind for m in messages for p in m.parts]


def test_keep_tool_results_only_in_the_latest_turns():
    kept = utils.keep_tool_results(_history(), 1)
    calls = [p.tool_call_id for m in kept for p in m.parts if p.part_kind == "tool-call"]
    returns = [p.tool_call_id for m in kept for p in m.parts if p.part_kind == "tool-return"]
    assert calls == returns == ["c2"]
    text = " ".join(str(getattr(p, "content", "")) for m in kept for p in m.parts)
    for i in range(3):
        assert f"question {i}" in text and f"answer {i}" in text
    assert "checking 0" in text  # text next to an old tool call stays
    assert _kinds(kept)[0] == "system-prompt"  # the dynamic system prompt slot is kept


def test_zero_tool_turns_drops_every_old_tool_part():
    kept = utils.keep_tool_results(_history(), 0)
    assert not {"tool-call", "tool-return"} & set(_kinds(kept))


def test_default_build_matches_bh_dev_trimming():
    history = _history()
    expected = utils.filter_thinking_from_history(utils.trim_history(history, max_tokens=64_000))
    assert utils.build_model_history(history) == expected
    assert utils.build_model_history(history, tool_turns=0) != expected


def test_dropping_tool_results_keeps_more_turns_in_the_same_budget():
    history = _history(6)
    for message in history:
        for part in message.parts:
            if part.part_kind == "tool-return":
                part.content = "x" * 4000
    budget = 2500
    with_tools = utils.build_model_history(history, max_tokens=budget)
    without = utils.build_model_history(history, tool_turns=0, max_tokens=budget)
    def turns(messages):
        return sum(1 for m in messages for p in m.parts if p.part_kind == "user-prompt")
    assert turns(without) > turns(with_tools)


def test_last_user_message():
    assert utils.last_user_message(_history()) == "question 2"
    assert utils.last_user_message([]) is None


def test_estimate_does_not_undercount_indic_text():
    assert utils.estimate_tokens("a" * 400) == 100
    assert utils.estimate_tokens("ਕ" * 400) == 200


def _long_history(turns, answer_chars):
    history = []
    for i in range(turns):
        parts = [UserPromptPart(f"question {i}")]
        if i == 0:
            parts.insert(0, SystemPromptPart("system"))
        history += [ModelRequest(parts=parts), ModelResponse(parts=[TextPart(f"answer {i} " + "x" * answer_chars)])]
    return history


def test_recent_turns_fit_the_budget_but_keep_at_least_three():
    history = _long_history(10, 4000)  # ~1k tokens per turn
    kept = utils.build_model_history(history, tool_turns=0, conversation_tokens=5000, min_turns=3)
    prompts = [p.content for m in kept for p in m.parts if p.part_kind == "user-prompt"]
    assert prompts == [f"question {i}" for i in range(6, 10)]
    assert kept[0].parts[0].part_kind == "system-prompt" and len(kept[0].parts) == 1
    big = _long_history(10, 40000)  # ~10k tokens per turn: the 3-turn minimum wins
    kept = utils.build_model_history(big, tool_turns=0, conversation_tokens=10000, min_turns=3)
    assert [p.content for m in kept for p in m.parts if p.part_kind == "user-prompt"] == [
        "question 7", "question 8", "question 9"]


def test_short_history_is_kept_whole_with_its_system_prompt():
    history = _long_history(2, 10)
    kept = utils.build_model_history(history, tool_turns=0, conversation_tokens=10000)
    assert kept == history


def test_loaded_skills_come_back_from_session_state():
    from agents.skills import load_skill, restore_loaded_skills
    d = deps()
    ctx = SimpleNamespace(deps=d)
    pending = sorted(d.skills.permitted - d.skills.active)
    assert "khc.grievances" in pending
    asyncio.run(load_skill(ctx, "khc.grievances"))
    assert d.session_state["skills"]["loaded"] == ["khc.grievances"]
    later = deps(skills=dict(d.session_state["skills"]))
    restore_loaded_skills(later)
    assert "khc.grievances" in later.skills.active
    assert session_state.render_state(later.session_state) == ""  # hidden from the model
