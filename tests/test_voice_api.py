"""Voice HTTP/streaming integration on the shared tools; all model responses are scripted.

Ported from voice-oan-api tests/test_ffs_voice.py. Exercises the Sarvam contract
(POST /v1/chat/completions, X-* headers, {audio, end_interaction, language} JSON),
voice auth, routing fallback, voice history and voice-only telemetry.
"""
import asyncio
from contextlib import contextmanager, nullcontext
import json
import time
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import jwt
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
import pytest

from agents.model_registry import get_registry
from agents.skill_catalog import get_catalog
from app.config import settings
from app.services import ffs
from app.services.model_routing import ModelRouteDecision
from tests.test_ffs import USER, setup  # noqa: F401  (pytest fixture)

TENANT, CALLER = "test-tenant", "test-user"


@pytest.fixture
def voice_api(setup, monkeypatch):  # noqa: F811
    import agents.voice as voice_agent_module
    from app.auth import jwt_auth
    from app.voice import completion_telemetry, completions, history, router, service
    from app.voice import telemetry as voice_telemetry

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt_auth, "public_key", private_key.public_key())
    monkeypatch.setattr(settings, "voice_auth_enabled", True)
    monkeypatch.setattr(voice_agent_module, "format_vector_schemes_prompt_block", lambda: {
        "vector_scheme_count": 1, "vector_schemes_bullets": "FFS", "vector_schemes_identifiers": "ffs"})
    monkeypatch.setattr(history, "cache", setup.memory)
    # Production traces voice runs to Langfuse; tests must never export spans there.
    monkeypatch.setattr(voice_agent_module.voice_agent, "instrument", False)

    observations, telemetry, history_reads = [], [], []

    @contextmanager
    def observe(**kwargs):
        observations.append(kwargs)
        yield SimpleNamespace(update=lambda **kw: observations.append(kw))

    monkeypatch.setattr(completions, "safe_start_observation", observe)
    monkeypatch.setattr(service, "safe_start_agent_observation", observe)
    for module in (completions, service):
        monkeypatch.setattr(module, "safe_propagate_attributes", lambda **kw: nullcontext())

    async def send(payload):
        telemetry.append(payload)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(voice_telemetry, "send_voice_telemetry", send)

    async def read_history(*args, **kwargs):
        history_reads.append(args[0])
        return await history.get_voice_history(*args, **kwargs)

    monkeypatch.setattr(completions, "get_voice_history", read_history)

    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    app = completion_telemetry.CompletionTelemetryMiddleware(app, path="/api/v1/chat/completions")

    def token(**claims):
        return jwt.encode({"sub": USER["sub"], "exp": int(time.time()) + 300,
                           "jti": uuid.uuid4().hex, **claims}, private_key, algorithm="RS256")

    bearer = token()
    headers = {"Authorization": "Bearer " + bearer, "X-Tenant-ID": TENANT,
               "X-User-ID": CALLER, "X-Session-ID": "voice-ffs-" + uuid.uuid4().hex,
               "X-Language": "en"}
    return SimpleNamespace(app=app, router=router, auth=jwt_auth, service=service, history=history,
                           headers=headers, token=token, bearer=bearer, observations=observations,
                           telemetry=telemetry, history_reads=history_reads)


class FakeRouter:
    def __init__(self, primary):
        self.primary, self.stored = primary, []

    async def resolve(self, use_case, session_id, *, has_history=False):
        assert use_case == "voice"
        return ModelRouteDecision(self.primary, "offline-test", "offline-test")

    async def store(self, use_case, session_id, alias):
        assert use_case == "voice"
        self.stored.append(alias)


def install_model(voice_api, monkeypatch, model, fallback=None):
    """Route the call to `model`; with `fallback`, start on gemma and fall back to azure."""
    primary = "gemma_vllm" if fallback else "azure_gpt54_mini"
    fake = FakeRouter(primary)
    monkeypatch.setattr(voice_api.service, "get_model_router", lambda: fake)
    monkeypatch.setattr(get_registry(), "get_model", lambda alias: model if alias == primary else fallback)
    return fake


def test_runtime_model_fallback_cannot_send_a_second_otp(voice_api, setup, monkeypatch):  # noqa: F811
    attempts = []

    async def failing(messages, info):
        attempts.append("primary")
        if len(attempts) == 1:
            yield {0: DeltaToolCall(name="ffs_send_otp", json_args=json.dumps({
                "farmer_id": "99999999999", "state_lgd_code": "27"}), tool_call_id="primary-otp")}
        else:
            raise TimeoutError("Simulated model failure after OTP send")

    async def fallback(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            yield {0: DeltaToolCall(name="ffs_send_otp", json_args=json.dumps({
                "farmer_id": "99999999999", "state_lgd_code": "27"}), tool_call_id="fallback-otp")}
        else:
            assert returns[-1].content["status"] == "already_started"
            yield "Please say the six OTP digits one at a time."

    router = install_model(voice_api, monkeypatch, FunctionModel(stream_function=failing),
                           FunctionModel(stream_function=fallback))
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=voice_api.headers,
            json={"messages": [{"role": "user", "content": "FFS for my farmer ID"}], "stream": True})
        assert response.status_code == 200 and "[DONE]" in response.text
    assert attempts == ["primary", "primary"]
    assert setup.backend.count("loginByFarmerId") == 1
    assert router.stored == ["azure_gpt54_mini"]  # Error fallback sticks for the call.


def test_voice_tools_are_the_shared_tools_minus_disabled(voice_api, monkeypatch):
    seen = {}

    async def stream(messages, info):
        seen["tools"] = {tool.name for tool in info.function_tools}
        yield "Hello."

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=voice_api.headers,
            json={"messages": [{"role": "user", "content": "hello"}], "stream": False})
    assert response.status_code == 200
    catalog = get_catalog()
    voice = catalog.manifest.agents["voice"]
    expected = catalog.tools_for(set(voice.enabled_skills)) - set(voice.disabled_tools)
    assert seen["tools"] == expected | {"submit_feedback"}
    assert "load_skill" not in seen["tools"] and "analyze_crop_image" not in seen["tools"]


@pytest.mark.parametrize("stream", [False, True])
def test_auth_disabled_bearerless_voice_completes_ffs_login_and_entitlement(voice_api, setup, monkeypatch, stream):  # noqa: F811
    monkeypatch.setattr(settings, "voice_auth_enabled", False)
    voice_api.headers.pop("Authorization")
    run_http_flow(voice_api, setup, monkeypatch, stream)
    assert setup.backend.count("loginByFarmerId") == 1
    assert setup.backend.count("validateLoginByFarmerId") == 1
    assert setup.backend.count("getRecommendationsByGFRV4") == 1


def test_auth_enabled_bearerless_voice_still_rejected(voice_api, setup):  # noqa: F811
    headers = {key: value for key, value in voice_api.headers.items() if key != "Authorization"}
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=headers,
            json={"messages": [{"role": "user", "content": "I want FFS fertilizer"}], "stream": False})
    assert response.status_code == 401
    assert not setup.backend.calls
    # The rejected request is still counted for the dashboard.
    details = voice_api.telemetry[-1]["events"][0]["edata"]["eks"]["target"]["networkApiDetails"]
    assert details["statusCode"] == 401 and details["output"]["outcome"] == "rejected"


def test_unsupported_language_header_is_rejected(voice_api, setup):  # noqa: F811
    headers = {**voice_api.headers, "X-Language": "xx"}
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=headers,
            json={"messages": [{"role": "user", "content": "hello"}], "stream": False})
    assert response.status_code == 400


def test_odia_header_uses_odia_prompt_and_answers_in_od(voice_api, monkeypatch):
    seen = {}

    async def stream(messages, info):
        seen["prompt"] = info.instructions or ""
        yield "ନମସ୍କାର"

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    headers = {**voice_api.headers, "X-Language": "od"}
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=headers,
            json={"messages": [{"role": "user", "content": "hello"}], "stream": False})
    assert response.status_code == 200
    spoken = json.loads(response.json()["choices"][0]["message"]["content"])
    assert spoken["language"] == "od"
    # Sarvam's "od" is bharat's "or": the call runs on voice/voice_or.md.
    odia_prompt = (get_catalog().root / "voice/voice_or.md").read_text()
    english_prompt = (get_catalog().root / "voice/voice_en.md").read_text()
    first_line = odia_prompt.splitlines()[0]
    assert first_line != english_prompt.splitlines()[0]
    assert seen["prompt"].startswith(first_line)


VOICE_LANGS = get_catalog().manifest.agents["voice"].languages
PROMPT_CONTEXT = {"today_date": "Thursday, 05 November 2026", "crop_season": "Rabi", "last_weekday_table": "",
                  "vector_scheme_count": 0, "vector_schemes_bullets": "", "vector_schemes_identifiers": "",
                  "khc_service_note": ""}


def full_prompt(agent, lang):
    from agents.skill_policy import SkillState
    from agents.skills import render_prompt
    return render_prompt(SkillState(agent=agent), lang, dict(PROMPT_CONTEXT))


@pytest.mark.parametrize("lang", VOICE_LANGS)
def test_voice_prompt_is_voice_base_plus_the_shared_feature_text(lang):
    voice, chat = full_prompt("voice", lang), full_prompt("chat", lang)
    catalog = get_catalog()
    # Base prompt differs per channel: speaking rules vs writing rules.
    assert voice.startswith(catalog.render_global(lang, PROMPT_CONTEXT, "voice"))
    assert "FEATURE WORKFLOWS ON A CALL" in voice and "FEATURE WORKFLOWS ON A CALL" not in chat
    # Feature instructions are the same files: every enabled feature's localized text is in both.
    for skill in catalog.manifest.agents["voice"].enabled_skills:
        _, body = catalog.document(skill, lang, "voice")
        lines = [line for line in body.strip().splitlines() if line.strip() and "{" not in line]
        if not lines:
            continue  # Body is only an include of a shared file.
        first_line = lines[0]
        assert first_line in voice and first_line in chat, (skill, first_line)
    for name in ("ffs_send_otp", "ffs_verify_otp", "ffs_get_entitlement", "ffs_find_shops", "ffs_create_booking"):
        assert name in voice
    assert "Farmer ID" in voice and "{%" not in voice and "search_videos" not in voice


@pytest.mark.parametrize("lang", VOICE_LANGS)
def test_call_only_notes_reach_voice_and_never_chat(lang):
    voice, chat = full_prompt("voice", lang), full_prompt("chat", lang)
    for note in ("first read back all eleven Farmer ID", "**On a call:**", "KHC on a call"):
        assert note in voice and note not in chat, note
    # Shared knowledge from the old voice prompt now reaches chat too.
    for shared in ("Crop suitability questions", "IMD data for the [location] is not updated",
                   "share the ticket number from the response"):
        assert shared in voice and shared in chat, shared


def test_voice_prompts_only_name_tools_the_voice_agent_has():
    catalog = get_catalog()
    voice = catalog.manifest.agents["voice"]
    available = catalog.tools_for(set(voice.enabled_skills)) - set(voice.disabled_tools) | {"submit_feedback"}
    for lang in voice.languages:
        base = catalog.render_global(lang, PROMPT_CONTEXT, "voice")
        named = {name for name in catalog.tools if f"`{name}" in base}
        assert named <= available, (lang, named - available)


def scripted_model():
    inputs, steps = [], []

    async def stream(messages, info):
        inputs.append(str(messages))
        index = len(steps)
        steps.append(index)
        schemas = {tool.name: tool for tool in info.function_tools}
        assert "otp" in schemas["ffs_verify_otp"].parameters_json_schema["properties"]
        calls = {
            0: ("ffs_send_otp", {"farmer_id": "99999999999", "state_lgd_code": "27"}),
            2: ("ffs_verify_otp", {"otp": "123456"}),
            4: ("ffs_get_entitlement", {"village_ref": "village-1", "crop_name": "maize", "crop_code": "0101000"}),
            6: ("ffs_find_shops", {"order": [{"product": "urea", "product_id": 1089, "bags": 1}]}),
        }
        if index == 8:
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            shops = next(p.content["data"]["shops"] for p in reversed(returns) if p.tool_name == "ffs_find_shops")
            calls[index] = ("ffs_create_booking", {"shop_ref": shops[0]["shop_ref"], "confirmed": True})
        if index in calls:
            name, args = calls[index]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"ffs-{index}")}
        else:
            yield "FFS details received."

    return FunctionModel(stream_function=stream), inputs, steps


def run_http_flow(voice_api, setup, monkeypatch, stream):  # noqa: F811
    model, inputs, steps = scripted_model()
    install_model(voice_api, monkeypatch, model)
    queries = ["Yes, farmer ID 99999999999 and Maharashtra are correct", "one two three four five six",
               "maize on all plots", "one bag of urea", "Yes, book at the first shop"]
    with TestClient(voice_api.app) as client:
        for query in queries:
            # Same envelope as Sarvam: latest user message only, plus session headers.
            response = client.post("/api/v1/chat/completions", headers=voice_api.headers, json={
                "model": "bharatvistaar-voice", "messages": [{"role": "user", "content": query}], "stream": stream})
            assert response.status_code == 200, response.text
            if stream:
                assert response.text.endswith("data: [DONE]\n\n")
                events = [json.loads(line[6:]) for line in response.text.splitlines()
                          if line.startswith("data: ") and line != "data: [DONE]"]
                content = [e["choices"][0]["delta"]["content"] for e in events
                           if "content" in e["choices"][0]["delta"]][-1]
            else:
                content = response.json()["choices"][0]["message"]["content"]
            spoken = json.loads(content)
            assert spoken["language"] == "en" and spoken["end_interaction"] is False
            assert "FFS details received." in spoken["audio"]
        session_id = voice_api.headers["X-Session-ID"]
        state = client.portal.call(ffs.get_session, session_id)
        history = client.portal.call(voice_api.history.get_voice_history, session_id)
        assert "FFS workflow:" not in str(history)
        assert all(text.count("FFS workflow:") <= 1 for text in inputs)
        assert "one two three four five six" in str(inputs)
        for secret in (state["token"], state["user"]["userAadhaarHash"]):
            assert secret not in str(inputs) + str(history)
        assert "backend_exchanges" not in str(inputs)
        assert "owner" not in state
        # Voice history lives under its own key, never chat's "{session}__SVA".
        keys = {key for _namespace, key in setup.memory.values}
        assert f"{session_id}_VOICE_HISTORY" in keys and f"{session_id}__SVA" not in keys
        # Refreshed API token must not lock an authenticated FFS session out.
        if "Authorization" in voice_api.headers:
            headers = {**voice_api.headers, "Authorization": "Bearer " + voice_api.token()}
            response = client.post("/api/v1/chat/completions", headers=headers, json={
                "messages": [{"role": "user", "content": "thanks"}], "stream": stream})
            assert response.status_code == 200
    assert setup.backend.count("loginByFarmerId") == 1
    assert setup.backend.count("validateLoginByFarmerId") == 1
    assert setup.backend.count("getRecommendationsByGFRV4") == 1
    assert len(setup.backend.booking_payloads) == 1
    lines = setup.backend.booking_payloads[0]["bookingRequest"][0]["productLines"]
    assert [(p["productId"], p["noBagsRequested"]) for p in lines] == [(1089, 1)]
    # Voice telemetry only: request counts, the welcome turn and one event per turn.
    eids = [event["eid"] for payload in voice_api.telemetry for event in payload["events"]]
    assert eids.count("OE_VOICE_RESPONSE") >= len(queries) + 1
    assert {event["channel"] for payload in voice_api.telemetry for event in payload["events"]} <= {
        settings.voice_telemetry_channel, "BharatVistaar-voice"}
    assert any(o.get("name") for o in voice_api.observations)
    return state


@pytest.mark.parametrize("stream", [False, True])
def test_voice_endpoint_books_explicit_order_and_stores_history_once(voice_api, setup, monkeypatch, stream):  # noqa: F811
    run_http_flow(voice_api, setup, monkeypatch, stream)


def test_verified_bearer_refresh_and_forged_signature(voice_api):
    user = asyncio.run(voice_api.auth.get_current_user(voice_api.bearer))
    refreshed = asyncio.run(voice_api.auth.get_current_user(voice_api.token()))
    assert user["sub"] == refreshed["sub"] == USER["sub"]
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"sub": USER["sub"], "exp": int(time.time()) + 300}, forged_key, algorithm="RS256")
    with pytest.raises(HTTPException) as error:
        asyncio.run(voice_api.auth.get_current_user(forged))
    assert error.value.status_code == 401


def _post(voice_api, query, stream=False, headers=None):
    with TestClient(voice_api.app) as client:
        response = client.post("/api/v1/chat/completions", headers=headers or voice_api.headers,
            json={"messages": [{"role": "user", "content": query}], "stream": stream})
    assert response.status_code == 200, response.text
    if not stream:
        return [json.loads(response.json()["choices"][0]["message"]["content"])]
    return [json.loads(json.loads(line[6:])["choices"][0]["delta"]["content"])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
            and "content" in json.loads(line[6:])["choices"][0]["delta"]]


def test_first_turn_speaks_hold_message_and_recording_notice_only_once(voice_api, monkeypatch):
    from app.voice.languages import get_language

    async def stream(messages, info):
        yield "Wheat needs irrigation now."

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    english = get_language("en")
    first = _post(voice_api, "wheat irrigation", stream=True)
    # The hold message is streamed before the model runs, then repeated at the front of each chunk.
    assert first[0]["audio"] == f"{english.hold_message} {english.recording_disclaimer}"
    assert first[-1]["audio"].startswith(english.hold_message)
    assert first[-1]["audio"].endswith("Wheat needs irrigation now.")
    second = _post(voice_api, "and for maize?")
    assert second[-1]["audio"] == "Wheat needs irrigation now."


def test_slow_answer_gets_one_delay_message(voice_api, monkeypatch):
    from app.voice.languages import get_language

    monkeypatch.setattr(settings, "voice_hold_message_enabled", False)
    monkeypatch.setattr(settings, "voice_delay_message_after_seconds", 0.05)

    async def stream(messages, info):
        await asyncio.sleep(0.3)
        yield "Here is the answer."

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    chunks = _post(voice_api, "question", stream=True)
    delay = get_language("en").delay_message
    assert sum(delay in chunk["audio"] for chunk in chunks[:1]) == 1
    assert chunks[-1]["audio"].endswith("Here is the answer.") and delay in chunks[-1]["audio"]


def test_feedback_only_after_asking_then_ends_call_with_telefeedback(voice_api, monkeypatch):
    feedback_call = {0: DeltaToolCall(name="submit_feedback", json_args=json.dumps(
        {"feedback_type": "like", "feedback_text": "good"}))}
    # Turn 1: tool call (refused), then the feedback question. Turn 2: tool call, then goodbye.
    script = iter([feedback_call, "Was this call helpful? Please share your feedback.",
                   feedback_call, "Thank you for calling."])

    async def stream(messages, info):
        yield next(script)

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    # Premature: the previous turn did not ask for feedback, so the tool refuses.
    early = _post(voice_api, "bye")[-1]
    assert early["end_interaction"] is False
    assert "feedback" in early["audio"].lower()
    # The assistant's last turn asked for feedback, so this one is accepted.
    late = _post(voice_api, "It was helpful")[-1]
    assert late["end_interaction"] is True
    events = [e for payload in voice_api.telemetry for e in payload["events"]]
    feedback = [e for e in events if e["edata"]["eks"].get("target", {}).get("type") == "TeleFeedback"]
    assert len(feedback) == 1
    details = feedback[0]["edata"]["eks"]["target"]["telefeedbackDetails"]
    assert details == {"feedbackText": "good", "sessionId": voice_api.headers["X-Session-ID"], "feedbackType": "like"}


def test_hold_only_reply_is_retried_into_a_real_answer(voice_api, monkeypatch):
    replies = iter(["Let me check that for you. One moment please.", "Urea dose is two bags per acre."])

    async def stream(messages, info):
        yield next(replies)

    install_model(voice_api, monkeypatch, FunctionModel(stream_function=stream))
    spoken = _post(voice_api, "urea dose")[-1]
    assert spoken["audio"].endswith("Urea dose is two bags per acre.")
    assert "One moment" not in spoken["audio"]
