"""Runtime event primitives for the Operational State Graph (OSG)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventType(str, enum.Enum):
    SERVICE_CALL = "service_call"
    MESSAGE_SEND = "message_send"
    MESSAGE_RECEIVE = "message_receive"
    STATE_TRANSITION = "state_transition"
    FAILURE_PROPAGATION = "failure_propagation"
    TIMEOUT_EXPIRY = "timeout_expiry"
    RETRY_ATTEMPT = "retry_attempt"
    CIRCUIT_BREAKER_TRIP = "circuit_breaker_trip"


class EventStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


class RuntimeEvent(BaseModel):
    """A runtime event observed in the target system (OSG primitive)."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    source_service: UUID
    target_service: Optional[UUID] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: Optional[float] = None
    status: EventStatus = EventStatus.SUCCESS
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_event: Optional[UUID] = None
    causal_predecessors: set[UUID] = Field(default_factory=set)
    payload_fingerprint: bytes = b""
    anomaly_score: float = Field(ge=0.0, le=1.0, default=0.0)
