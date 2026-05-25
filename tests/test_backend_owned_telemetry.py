import asyncio

import jwt

from app.auth import jwt_auth
from app.routers.token import AuthRequest, create_auth_token
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_answer_event,
    create_chat_error_event,
    create_chat_feedback_event,
    create_chat_question_event,
    resolve_telemetry_identity,
)


def test_resolve_telemetry_identity_prefers_guest_fingerprint_context():
    current_user = {
        "sub": "guest:fp_123",
        "channel": "BharatVistaar",
        "client_code": "BharatVistaar",
        "telemetry_context": {
            "uid": "guest",
            "did": "fp_123",
            "channel": "BharatVistaar",
            "pdata_id": "BharatVistaar",
            "pdata_ver": "v0.1",
        },
        "metadata": {"fingerprint_id": "fp_123"},
    }

    identity = resolve_telemetry_identity(current_user)

    assert identity == {
        "uid": "guest",
        "did": "fp_123",
        "channel": "BharatVistaar",
        "pdata_id": "BharatVistaar",
        "pdata_ver": "v0.1",
    }


def test_chat_telemetry_builders_match_frontend_event_shapes():
    current_user = {
        "telemetry_context": {
            "uid": "guest",
            "did": "fp_123",
            "channel": "BharatVistaar",
            "pdata_id": "BharatVistaar",
            "pdata_ver": "v0.1",
        },
        "metadata": {"fingerprint_id": "fp_123"},
    }

    question = create_chat_question_event(current_user, "q1", "question?", "s1")
    answer = create_chat_answer_event(current_user, "q1", "question?", "answer", "s1")
    error = create_chat_error_event(current_user, "q1", "s1", "boom", "question?")
    feedback = create_chat_feedback_event(
        current_user,
        "q1",
        "s1",
        "Liked the response",
        "like",
        "question?",
        "answer",
    )

    payload = TelemetryRequest(events=[question, answer, error, feedback]).model_dump()
    targets = [event["edata"]["eks"]["target"] for event in payload["events"]]

    assert [target["type"] for target in targets] == [
        "Question",
        "QuestionResponse",
        "Error",
        "Feedback",
    ]
    assert payload["events"][0]["uid"] == "guest"
    assert payload["events"][0]["did"] == "fp_123"
    assert payload["events"][0]["channel"] == "BharatVistaar"
    assert targets[1]["questionsDetails"]["answerText"] == "answer"
    assert payload["events"][2]["edata"]["eks"]["errorDetails"]["errorText"] == "boom"
    assert targets[3]["feedbackDetails"]["feedbackType"] == "like"


def test_guest_token_includes_bharat_vistaar_fingerprint_context():
    response = asyncio.run(
        create_auth_token(
            AuthRequest(
                fingerprint_id="fp_123",
                metadata={"platform": "web"},
            )
        )
    )

    decoded = jwt.decode(
        response.token,
        jwt_auth.public_key,
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_iss": False},
    )

    assert decoded["sub"] == "guest:fp_123"
    assert decoded["channel"] == "BharatVistaar"
    assert decoded["client_code"] == "BharatVistaar"
    assert decoded["auth_source"] == "guest_token"
    assert decoded["is_guest_user"] is True
    assert decoded["telemetry_context"]["did"] == "fp_123"
    assert decoded["telemetry_context"]["uid"] == "guest"
    assert decoded["metadata"]["fingerprint_id"] == "fp_123"
    assert decoded["metadata"]["surface"] == "public_chat"
