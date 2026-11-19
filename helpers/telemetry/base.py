"""Ekstep 2.2 telemetry envelope shared by the chat and voice channels.

Only the event format lives here. What each channel sends, under which channel/pdata
identity and to which endpoint, stays in helpers/telemetry/chat.py and
helpers/telemetry/voice.py with their own senders.
"""
from datetime import datetime
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, field_serializer
import secrets


def generate_telemetry_mid() -> str:
    return f"OE_{secrets.token_hex(16)}"

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
    OE_VOICE_RESPONSE = "OE_VOICE_RESPONSE"



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
    errorDetails: Optional[Dict[str, Any]] = None


class BaseEventData(BaseModel):
    """Base class for event-specific data"""
    pass


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
        return generate_telemetry_mid()
    
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
    if isinstance(event.edata.eks, BaseModel):
        event.edata.eks = event.edata.eks.model_dump(exclude_none=True)
    return event
