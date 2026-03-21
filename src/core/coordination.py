"""Coordination primitives: WorkItem, AgentBid, Claim, Question, ResourceBudget."""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkItemStatus(str, enum.Enum):
    OPEN = "open"
    BIDDING = "bidding"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"  # v3.3 D2


class ResourceBudget(BaseModel):
    """Resource budget for a work item or agent bid."""

    cpu_seconds: float = 0.0
    memory_mb: float = 0.0
    solver_ms: float = 0.0
    llm_tokens: int = 0
    wall_clock_ms: float = 0.0


class AgentBid(BaseModel):
    """An agent's bid to claim a work item."""

    agent_id: str
    work_item_id: UUID
    capability_match: float = Field(ge=0.0, le=1.0)
    estimated_cost: ResourceBudget = Field(default_factory=ResourceBudget)
    estimated_time: float = 0.0  # seconds
    expected_information_gain: float = Field(ge=0.0, le=1.0, default=0.0)
    current_load: float = Field(ge=0.0, le=1.0, default=0.0)
    agent_reliability: float = Field(ge=0.0, le=1.0, default=1.0)
    utility_score: float = 0.0


class WorkItem(BaseModel):
    """A unit of work posted to the coordination blackboard.

    v3.3 D2: ABANDONED status added.
    v3.3 D3: last_heartbeat for liveness detection.
    v3.3 A4: incident_id for multi-incident tracking.
    """

    item_id: UUID = Field(default_factory=uuid4)
    task_type: str
    scope: set[UUID] = Field(default_factory=set)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: float = 0.0
    attention_score: float = 0.0
    required_capabilities: set[str] = Field(default_factory=set)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    status: WorkItemStatus = WorkItemStatus.OPEN
    claimed_by: Optional[str] = None  # AgentID
    bids: list[AgentBid] = Field(default_factory=list)
    result: Optional[Any] = None
    attempt_count: int = 0
    max_attempts: int = 3
    deadline: datetime = Field(default_factory=lambda: utc_now() + timedelta(hours=1))
    incident_id: UUID = Field(default_factory=uuid4)  # v3.3 A4
    last_heartbeat: datetime = Field(default_factory=utc_now)  # v3.3 D3


class Claim(BaseModel):
    """An agent's claim on a work item."""

    claim_id: UUID = Field(default_factory=uuid4)
    agent_id: str
    work_item_id: UUID
    claimed_at: datetime = Field(default_factory=utc_now)


class Question(BaseModel):
    """A question posted to the blackboard for collaborative resolution."""

    question_id: UUID = Field(default_factory=uuid4)
    asked_by: str  # AgentID
    question_type: str
    context: dict[str, Any] = Field(default_factory=dict)
    answers: list[dict[str, Any]] = Field(default_factory=list)
    resolved: bool = False
