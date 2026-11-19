"""Voice channel telemetry builders (Sarvam phone calls).

Payloads match what voice-oan-api sent, field for field, because the dashboard
processor routes on them: OE_VOICE_RESPONSE per turn, OE_ITEM_RESPONSE with
target.type=TeleFeedback for call feedback, and the VOICE_API request-count event.

The voice target deliberately has only its own fields. Chat's Target also carries
ttsResponseDetails/asrResponseDetails; the processor treats any present key (even
null) as a TTS/ASR event, so voice events must never include them.
"""
from typing import Any, Dict, Optional
import uuid

from pydantic import BaseModel

from helpers.telemetry.base import EventType, TelemetryEvent, create_event

# Fixed identity of the request-count event; the dashboard's Service Check > Voice
# row filters on it.
COMPLETION_EVENT_CHANNEL = "BharatVistaar-voice"
VOICE_PDATA_ID = "BharatVistaar-voice"
FEEDBACK_PDATA_ID = "BharatVistaar"


class VoiceTarget(BaseModel):
    id: str
    ver: str
    type: str
    parent: Optional[Dict[str, str]] = None
    questionsDetails: Optional[Dict[str, Any]] = None
    telefeedbackDetails: Optional[Dict[str, Any]] = None


def generate_voice_question_id() -> str:
    """Return a new UUID string for voice question / turn correlation (qid)."""
    return str(uuid.uuid4())


def create_voice_response_event(
    *,
    uid: str,
    session_id: str,
    channel: str,
    question_text: Optional[str] = None,
    qid: Optional[str] = None,
    source_lang: str = "",
    target_lang: str = "",
    response_text: Optional[str] = None,
    did: str = "default-email",
    pdata_id: str = VOICE_PDATA_ID,
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """OE_VOICE_RESPONSE: one event per voice turn (question + optional answer).

    questionText is omitted when question_text is None (the scripted welcome turn).
    """
    details: Dict[str, Any] = {}
    if question_text is not None:
        details["questionText"] = question_text
    if response_text is not None:
        details["responseText"] = response_text
    if source_lang:
        details["sourceLang"] = source_lang
    if target_lang:
        details["targetLang"] = target_lang
    target = VoiceTarget(
        id="default",
        ver="v0.1",
        type="VoiceQuestion",
        parent={"id": "p1", "type": "default"},
        questionsDetails=details,
    )
    return create_event(
        event_type=EventType.OE_VOICE_RESPONSE,
        # A dict, so create_event keeps the nulls exactly as voice always sent them.
        event_data={"target": target.model_dump(), "qid": qid or generate_voice_question_id()},
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp,
    )


def create_feedback_event(
    *,
    session_id: str,
    feedback_type: str,
    feedback_text: str,
    uid: str,
    channel: str,
    did: str = "DEFAULT-USER",
    pdata_id: str = FEEDBACK_PDATA_ID,
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """OE_ITEM_RESPONSE with target.type=TeleFeedback; feedback_type is like/dislike."""
    target = VoiceTarget(
        id="default",
        ver="v0.1",
        type="TeleFeedback",
        parent={"id": "p1", "type": "default"},
        telefeedbackDetails={
            "feedbackText": feedback_text or "",
            "sessionId": session_id,
            "feedbackType": feedback_type,
        },
    )
    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data={"target": target.model_dump()},
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp,
    )


def create_voice_completion_payload(
    *,
    url: str,
    status: int,
    latency_ms: int,
    outcome: str,
    error_type: Optional[str],
    timestamp: int,
) -> Dict[str, Any]:
    """Request-count event for the dashboard's Service Check > Voice row.

    One per POST /v1/chat/completions, carrying no bodies, headers or user/session ids.
    """
    return {
        "id": "ekstep.telemetry",
        "ver": "2.2",
        "ets": timestamp,
        "events": [{
            "eid": "OE_ITEM_RESPONSE",
            "ver": "2.2",
            "mid": f"OE_{uuid.uuid4()}",
            "ets": timestamp,
            "channel": COMPLETION_EVENT_CHANNEL,
            "pdata": {"id": VOICE_PDATA_ID, "ver": "v0.1"},
            "gdata": {"id": "voice", "ver": "v0.1"},
            "uid": "voice-service",
            "did": "voice-service",
            "sid": "",
            "edata": {"eks": {
                "type": "EXT_API_CALL",
                "target": {
                    "id": "voice_chat_completions",
                    "ver": "v0.1",
                    "type": "NetworkApi",
                    "networkApiDetails": {
                        "apiType": "VOICE_API",
                        "apiService": "voice_chat_completions",
                        "method": "POST",
                        "url": url,
                        "statusCode": status,
                        "latencyMs": latency_ms,
                        "success": outcome == "success",
                        "output": {"outcome": outcome, "errorType": error_type},
                    },
                },
            }},
        }],
    }
