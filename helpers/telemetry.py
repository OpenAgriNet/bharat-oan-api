from datetime import datetime
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, field_serializer
import hashlib
import time
import random
import os

class EventType(str, Enum):
    """Types of telemetry events"""
    OE_START = "OE_START"
    OE_END = "OE_END"
    OE_ITEM_RESPONSE = "OE_ITEM_RESPONSE"
    OE_INTERACT = "OE_INTERACT"
    OE_ASSESS = "OE_ASSESS"
    OE_LEVEL_SET = "OE_LEVEL_SET"
    OE_MEDIA = "OE_MEDIA"
    OE_TRANSLATION = "OE_TRANSLATION"
    OE_MODERATION = "OE_MODERATION"



class PData(BaseModel):
    """Producer data - information about the system that generated the event"""
    id: str
    ver: str
    pid: Optional[str] = None


class GData(BaseModel):
    """Game/content specific data"""
    id: str
    ver: str


class Target(BaseModel):
    """Target information for item response events"""
    id: str
    ver: str
    type: str
    parent: Optional[Dict[str, str]] = None
    questionsDetails: Optional[Dict[str, Any]] = None
    ttsResponseDetails: Optional[Dict[str, Any]] = None
    asrResponseDetails: Optional[Dict[str, Any]] = None
    feedbackDetails: Optional[Dict[str, Any]] = None


class BaseEventData(BaseModel):
    """Base class for event-specific data"""
    pass


class ItemResponseEks(BaseEventData):
    """Extended data for OE_ITEM_RESPONSE events"""
    target: Target
    qid: str
    type: str
    state: str
    errorDetails: Optional[Dict[str, Any]] = None


class EndEventEks(BaseEventData):
    """Extended data for OE_END events"""
    progress: int
    stageid: str = ""
    length: float


class StartEventEks(BaseEventData):
    """Extended data for OE_START events"""
    pass


class MediaEventEks(BaseEventData):
    """Extended data for OE_MEDIA events"""
    type: str
    media_type: str
    media_id: str
    session_id: str
    storage: Dict[str, str]
    duration: Optional[float] = None


class TranslationEventEks(BaseEventData):
    """Extended data for OE_TRANSLATION events"""
    target: Target
    type: str

class QueryTranslationEventEks(BaseEventData):
    """Extended data for OE_QUERY_TRANSLATION events"""
    target: Target
    type: str


class ModerationEventEks(BaseEventData):
    """Extended data for OE_MODERATION events"""
    target: Target
    type: str
    # content_id: str
    # session_id: str
    # content_type: str
    # moderation_service: str
    # flagged: bool
    # action_taken: Optional[str] = None
    # reason: Optional[str] = None


class ApiCallEventEks(BaseEventData):
    """Extended data for OE_API_CALL events"""
    target: Target
    type: str


class InteractEventEks(BaseEventData):
    """Extended data for generic client interaction telemetry events"""
    eventName: str
    category: str
    clientTime: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EData(BaseModel):
    """Event specific data"""
    eks: Union[Dict[str, Any], BaseEventData]


class TelemetryEvent(BaseModel):
    """Individual telemetry event"""
    eid: Union[EventType, str]
    ver: str = "2.2"
    mid: str = ""
    ets: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    channel: str
    pdata: PData
    gdata: GData
    cdata: List[Dict[str, Any]] = Field(default_factory=list)
    uid: str
    sid: str = ""
    did: str
    edata: EData
    etags: Dict[str, List[Any]] = Field(default_factory=lambda: {"partner": []})
    
    @field_validator("mid", mode="before")
    def generate_mid_if_empty(cls, value):
        if value:
            return value
        random_str = f"{time.time()}{random.random()}"
        return f"OE_{hashlib.md5(random_str.encode()).hexdigest()}"
    
    @field_serializer("eid")
    def serialize_eid(self, eid: Union[EventType, str]) -> str:
        """Serialize eid to string value"""
        if isinstance(eid, EventType):
            return eid.value
        return eid


class TelemetryRequest(BaseModel):
    """Telemetry API request payload"""
    id: str = "ekstep.telemetry"
    ver: str = "2.2"
    ets: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    events: List[TelemetryEvent]




def create_event(
    event_type: EventType,
    event_data: Union[Dict[str, Any], BaseEventData],
    uid: str,
    channel: str = "BharatVistaar",
    did: str = "default-email",
    sid: str = "",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """
    Generic event creator function
    
    Args:
        event_type: Type of event
        event_data: Event-specific data
        uid: User ID
        channel: Channel name
        did: Device ID
        sid: Session ID
        pdata_id: Producer data ID
        pdata_ver: Producer data version
        gdata_id: Game/content data ID
        gdata_ver: Game/content data version
        timestamp: Event timestamp in milliseconds
        
    Returns:
        TelemetryEvent: A telemetry event
    """
    event = TelemetryEvent(
        eid=event_type,
        ets=timestamp if timestamp is not None else int(datetime.now().timestamp() * 1000),
        channel=channel,
        pdata=PData(id=pdata_id, ver=pdata_ver),
        gdata=GData(id=gdata_id, ver=gdata_ver),
        uid=uid,
        sid=sid,
        did=did,
        edata=EData(eks=event_data)
    )
    event.eid = event.eid.value
    event.edata.eks = event.edata.eks.model_dump(exclude_none=True)
    return event


def create_start_event(
    uid: str,
    channel: str = "BharatVistaar",
    did: str = "default-email",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates a start event for telemetry"""
    return create_event(
        event_type=EventType.OE_START,
        event_data=StartEventEks(),
        uid=uid,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )

def create_item_response_event(
    uid: str,
    qid: str,
    question_text: str,
    session_id: str,
    type: str = "CHOOSE",
    channel: str = "BharatVistaar",
    did: str = "default-email",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates an item response event for telemetry"""
    target = Target(
        id="default",
        ver="v0.1",
        type="Question",
        parent={"id": "p1", "type": "default"},
        questionsDetails={
            "questionText": question_text,
            "sessionId": session_id
        }
    )

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid,
            type=type,
            state=""
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )


def resolve_telemetry_identity(
    current_user: Dict[str, Any],
) -> Dict[str, str]:
    """Resolve telemetry identity from JWT claims with guest fallbacks."""
    telemetry_context = current_user.get("telemetry_context") or {}
    metadata = current_user.get("metadata") or {}
    if not isinstance(telemetry_context, dict):
        telemetry_context = {}
    if not isinstance(metadata, dict):
        metadata = {}

    uid = (
        current_user.get("mobile")
        or current_user.get("unique_id")
        or current_user.get("farmer_id")
        or telemetry_context.get("uid")
        or "guest"
    )
    did = (
        telemetry_context.get("did")
        or metadata.get("fingerprint_id")
        or current_user.get("sub")
        or "unknown-device"
    )
    channel = (
        os.getenv("TELEMETRY_CHANNEL")
        or telemetry_context.get("channel")
        or current_user.get("channel")
        or current_user.get("client_code")
        or "BharatVistaar"
    )

    return {
        "uid": str(uid),
        "did": str(did),
        "channel": str(channel),
        "pdata_id": str(telemetry_context.get("pdata_id") or "BharatVistaar"),
        "pdata_ver": str(telemetry_context.get("pdata_ver") or "v0.1"),
    }


def create_chat_question_event(
    current_user: Dict[str, Any],
    qid: str,
    question_text: str,
    session_id: str,
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    identity = resolve_telemetry_identity(current_user)
    return create_item_response_event(
        uid=identity["uid"],
        qid=qid,
        question_text=question_text,
        session_id=session_id,
        channel=identity["channel"],
        did=identity["did"],
        pdata_id=identity["pdata_id"],
        pdata_ver=identity["pdata_ver"],
        timestamp=timestamp,
    )


def create_chat_answer_event(
    current_user: Dict[str, Any],
    qid: str,
    question_text: str,
    answer_text: str,
    session_id: str,
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    identity = resolve_telemetry_identity(current_user)
    target = Target(
        id="default",
        ver="v0.1",
        type="QuestionResponse",
        parent={"id": "p1", "type": "default"},
        questionsDetails={
            "questionText": question_text,
            "answerText": answer_text,
            "sessionId": session_id,
        },
    )

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid,
            type="CHOOSE",
            state="",
        ),
        uid=identity["uid"],
        sid=session_id,
        channel=identity["channel"],
        did=identity["did"],
        pdata_id=identity["pdata_id"],
        pdata_ver=identity["pdata_ver"],
        timestamp=timestamp,
    )


def create_chat_error_event(
    current_user: Dict[str, Any],
    qid: str,
    session_id: str,
    error_text: str,
    question_text: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    identity = resolve_telemetry_identity(current_user)
    target = Target(
        id="default",
        ver="v0.1",
        type="Error",
        parent={"id": "p1", "type": "default"},
        questionsDetails={
            "questionText": question_text,
            "sessionId": session_id,
        } if question_text else {"sessionId": session_id},
    )

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid,
            type="CHOOSE",
            state="",
            errorDetails={"errorText": error_text, "sessionId": session_id},
        ),
        uid=identity["uid"],
        sid=session_id,
        channel=identity["channel"],
        did=identity["did"],
        pdata_id=identity["pdata_id"],
        pdata_ver=identity["pdata_ver"],
        timestamp=timestamp,
    )


def create_chat_feedback_event(
    current_user: Dict[str, Any],
    qid: str,
    session_id: str,
    feedback_text: str,
    feedback_type: str,
    question_text: str = "",
    answer_text: str = "",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    identity = resolve_telemetry_identity(current_user)
    target = Target(
        id="default",
        ver="v0.1",
        type="Feedback",
        parent={"id": "p1", "type": "default"},
        feedbackDetails={
            "feedbackText": feedback_text,
            "sessionId": session_id,
            "questionText": question_text,
            "answerText": answer_text,
            "feedbackType": feedback_type,
        },
    )

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid,
            type="CHOOSE",
            state="",
        ),
        uid=identity["uid"],
        sid=session_id,
        channel=identity["channel"],
        did=identity["did"],
        pdata_id=identity["pdata_id"],
        pdata_ver=identity["pdata_ver"],
        timestamp=timestamp,
    )


def create_ui_interact_event(
    current_user: Dict[str, Any],
    event_name: str,
    category: str,
    client_time: str,
    metadata: Optional[Dict[str, Any]] = None,
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    identity = resolve_telemetry_identity(current_user)
    event_metadata = metadata or {}
    session_id = str(
        event_metadata.get("session_id")
        or event_metadata.get("sessionId")
        or ""
    )

    return create_event(
        event_type=EventType.OE_INTERACT,
        event_data=InteractEventEks(
            eventName=event_name,
            category=category,
            clientTime=client_time,
            metadata=event_metadata,
        ),
        uid=identity["uid"],
        sid=session_id,
        channel=identity["channel"],
        did=identity["did"],
        pdata_id=identity["pdata_id"],
        pdata_ver=identity["pdata_ver"],
        timestamp=timestamp,
    )


def create_end_event(
    uid: str,
    progress: int,
    length: float,
    session_id: str = "",
    channel: str = "BharatVistaar",
    did: str = "default-email",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates an end event for telemetry"""
    return create_event(
        event_type=EventType.OE_END,
        event_data=EndEventEks(
            progress=progress,
            stageid="",
            length=length
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )


def create_audio_upload_event(
    session_id: str,
    bucket_name: str,
    file_key: str,
    uid: str = "system",
    channel: str = "BharatVistaar",
    did: str = "system",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates an audio upload event for telemetry"""
    return create_event(
        event_type=EventType.OE_MEDIA,
        event_data=MediaEventEks(
            type="AUDIO_UPLOAD",
            media_type="audio",
            media_id=file_key,
            session_id=session_id,
            storage={
                "bucket": bucket_name,
                "key": file_key
            }
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )


def create_translation_event(
    source_language: str,
    target_language: str,
    content_id: str,
    session_id: str,
    content_type: str,
    translation_service: str,
    success: bool = True,
    uid: str = "system",
    channel: str = "BharatVistaar",
    did: str = "system",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
    translated_text: str = None,
    original_text: str = None,
    chars_count: int = None,
) -> TelemetryEvent:
    """Creates a translation event for telemetry"""
    target = Target(
        id="default",
        ver="v0.1",
        type="Translation",
        parent={"id": "p1", "type": "default"},
        questionsDetails={
            "sourceLanguage": source_language,
            "targetLanguage": target_language,
            "contentId": content_id,
            "sessionId": session_id,
            "contentType": content_type,
            "translationService": translation_service,
            "translatedText": translated_text,
            "success": success,
            "originalText": original_text,
            "charsCount": chars_count
        }
    )
    return create_event(
        event_type=EventType.OE_TRANSLATION,
        event_data=TranslationEventEks(
            target=target,
            type="TEXT_TRANSLATION"
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )

def create_moderation_event(
    question_text: str,
    moderation_type: str,
    content_id: str,
    session_id: str,
    content_type: str,
    moderation_service: str,
    flagged: bool,
    category: Optional[str] = None,
    action: Optional[str] = None,
    uid: str = "system",
    channel: str = "BharatVistaar",
    did: str = "system",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates a moderation event for telemetry"""
    target = Target(
        id="default",
        ver="v0.1",
        type="Question",
        parent={"id": "p1", "type": "default"},
        questionsDetails={
            "questionText": question_text,
            "sessionId": session_id,
            "contentId": content_id,
            "contentType": content_type,
            "moderationService": moderation_service,
            "flagged": flagged,
            "category": category,
            "action": action
        }
    )
    return create_event(
        event_type=EventType.OE_MODERATION,
        event_data=ModerationEventEks(
            target=target,
            type=moderation_type
            # content_id=content_id,
            # session_id=session_id,
            # content_type=content_type,
            # moderation_service=moderation_service,
            # flagged=flagged,
            # action_taken=action_taken,
            # reason=reason
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )

def create_tts_event(
    success: bool,
    latency_ms: float,
    status_code: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    language: Optional[str] = None,
    session_id: str = "",
    text: Optional[str] = None,
    qid: Optional[str] = None,
    uid: str = "system",
    channel: str = "BharatVistaar",
    did: str = "system",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates a TTS event for telemetry."""
    tts_response_details = {
        "apiType": "TTS",
        "apiService": "bhashini",
        "success": success,
        "latencyMs": latency_ms,
        "statusCode": status_code,
        "errorCode": error_code,
        "errorMessage": error_message,
        "language": language,
        "sessionId": session_id,
        **({"text": text} if text else {}),
        **({"qid": qid} if qid else {})
    }
    
    questions_details = {
        **({"text": text} if text else {}),
        **({"qid": qid} if qid else {})
    }
    
    target = Target(
        id="bhashini_api",
        ver="v1.0",
        type="API_CALL",
        parent={"id": "bhashini", "type": "external_service"},
        ttsResponseDetails=tts_response_details,
        questionsDetails=questions_details if questions_details else None
    )

    error_details = None
    if not success:
        error_details = {
            "errorText": error_message or "error",
            "sessionId": session_id,
        }

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid or f"tts_{session_id}",
            type="BHASHINI_TTS",
            state="",
            errorDetails=error_details,
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )


def create_asr_event(
    success: bool,
    latency_ms: float,
    status_code: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    language: Optional[str] = None,
    session_id: str = "",
    text: Optional[str] = None,
    qid: Optional[str] = None,
    uid: str = "system",
    channel: str = "BharatVistaar",
    did: str = "system",
    pdata_id: str = "BharatVistaar",
    pdata_ver: str = "v0.1",
    gdata_id: str = "content_id",
    gdata_ver: str = "content_ver",
    timestamp: Optional[int] = None,
) -> TelemetryEvent:
    """Creates an ASR event for telemetry."""
    asr_response_details = {
        "apiType": "ASR",
        "apiService": "bhashini",
        "success": success,
        "latencyMs": latency_ms,
        "statusCode": status_code,
        "errorCode": error_code,
        "errorMessage": error_message,
        "language": language,
        "sessionId": session_id,
        **({"text": text} if text else {}),
        **({"qid": qid} if qid else {})
    }
    
    questions_details = {
        **({"text": text} if text else {}),
        **({"qid": qid} if qid else {})
    }
    
    target = Target(
        id="bhashini_api",
        ver="v1.0",
        type="API_CALL",
        parent={"id": "bhashini", "type": "external_service"},
        asrResponseDetails=asr_response_details,
        questionsDetails=questions_details if questions_details else None
    )

    error_details = None
    if not success:
        error_details = {
            "errorText": error_message or "error",
            "sessionId": session_id,
        }

    return create_event(
        event_type=EventType.OE_ITEM_RESPONSE,
        event_data=ItemResponseEks(
            target=target,
            qid=qid or f"asr_{session_id}",
            type="BHASHINI_ASR",
            state="",
            errorDetails=error_details,
        ),
        uid=uid,
        sid=session_id,
        channel=channel,
        did=did,
        pdata_id=pdata_id,
        pdata_ver=pdata_ver,
        gdata_id=gdata_id,
        gdata_ver=gdata_ver,
        timestamp=timestamp
    )
# TODO: Directly call the task isntead of this
# def log_audio_upload(
#     session_id: str,
#     bucket_name: str,
#     file_key: str,
#     token: str = "",
#     uid: str = "system",
#     timestamp: Optional[int] = None
# ) -> Dict:
#     """Logs an audio upload to telemetry"""
    
#     return send_telemetry.s(request=telemetry_request.model_dump()).apply_async()
