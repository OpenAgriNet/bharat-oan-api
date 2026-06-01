import asyncio

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import jwt_auth
from app.auth.jwt_auth import get_current_user
from app.routers import telemetry as telemetry_router
from app.routers.token import AuthRequest, create_auth_token
from helpers.telemetry import (
    TelemetryRequest,
    create_chat_answer_event,
    create_chat_error_event,
    create_chat_feedback_event,
    create_chat_question_event,
    create_frontend_compatible_item_batch,
    create_ui_interact_event,
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
        "fingerprint_details": {"device_id": "fp_123"},
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
    assert payload["events"][0]["edata"]["eks"]["fingerprint_details"] == {
        "device_id": "fp_123"
    }
    assert targets[1]["questionsDetails"]["answerText"] == "answer"
    assert targets[2]["errorDetails"]["errorText"] == "boom"
    assert targets[3]["feedbackDetails"]["feedbackType"] == "like"


def test_chat_item_batch_wraps_item_events_for_observability_ingestion():
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
    payload = TelemetryRequest(
        events=create_frontend_compatible_item_batch(question)
    ).model_dump()
    events = payload["events"]

    assert [event["eid"] for event in events] == [
        "OE_START",
        "OE_ITEM_RESPONSE",
        "OE_END",
    ]
    assert events[1]["edata"]["eks"]["target"]["type"] == "Question"
    assert all(event["mid"].startswith("OE_") for event in events)
    assert all(event["sid"] == "s1" for event in events)
    assert all(
        event["edata"]["eks"]["fingerprint_details"] == {"device_id": "fp_123"}
        for event in events
    )


def test_generic_ui_interact_builder_preserves_metadata():
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
    metadata = {"notification_id": "notif_123", "session_id": "s1"}

    event = create_ui_interact_event(
        current_user=current_user,
        event_name="notification_clicked",
        category="notification",
        client_time="2026-05-28T10:15:30.000Z",
        metadata=metadata,
    )
    payload = TelemetryRequest(events=[event]).model_dump()
    encoded_event = payload["events"][0]
    eks = encoded_event["edata"]["eks"]

    assert encoded_event["eid"] == "OE_INTERACT"
    assert encoded_event["uid"] == "guest"
    assert encoded_event["did"] == "fp_123"
    assert encoded_event["channel"] == "BharatVistaar"
    assert encoded_event["sid"] == "s1"
    assert eks == {
        "eventName": "notification_clicked",
        "category": "notification",
        "clientTime": "2026-05-28T10:15:30.000Z",
        "metadata": metadata,
    }


def test_generic_ui_telemetry_events_route_enqueues_one_batch(monkeypatch):
    captured_payloads = []
    app = FastAPI()

    async def fake_current_user():
        return {
            "telemetry_context": {
                "uid": "guest",
                "did": "fp_123",
                "channel": "BharatVistaar",
                "pdata_id": "BharatVistaar",
                "pdata_ver": "v0.1",
            },
            "metadata": {"fingerprint_id": "fp_123"},
        }

    async def fake_send_telemetry(payload):
        captured_payloads.append(payload)
        return {"status_code": 200}

    monkeypatch.setattr(telemetry_router, "send_telemetry", fake_send_telemetry)
    app.dependency_overrides[get_current_user] = fake_current_user
    app.include_router(telemetry_router.router, prefix="/api")

    client = TestClient(app)
    response = client.post(
        "/api/telemetry/events",
        json=[
            {
                "event_name": "location_allowed",
                "category": "location",
                "time": "2026-05-28T10:15:30.000Z",
                "metadata": {"action": "allow"},
            },
            {
                "event_name": "notification_clicked",
                "category": "notification",
                "time": "2026-05-28T10:15:30.000Z",
                "metadata": {"notification_id": "notif_123"},
            },
        ],
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "count": 2}
    assert len(captured_payloads) == 1
    events = captured_payloads[0]["events"]
    assert [event["eid"] for event in events] == ["OE_INTERACT", "OE_INTERACT"]
    assert events[0]["edata"]["eks"]["metadata"] == {"action": "allow"}
    assert events[1]["edata"]["eks"]["eventName"] == "notification_clicked"
    assert events[1]["edata"]["eks"]["metadata"] == {"notification_id": "notif_123"}


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
