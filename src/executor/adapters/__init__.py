"""Executor adapters for external systems."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ExecutionResult(BaseModel):
    execution_id: UUID = Field(default_factory=uuid4)
    status: ExecutionStatus = ExecutionStatus.PENDING
    adapter_type: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    rollback_available: bool = True


from src.executor.adapters.git import GitAdapter
from src.executor.adapters.container import ContainerAdapter
from src.executor.adapters.ci import CIAdapter
from src.executor.adapters.iac import IaCAdapter
from src.executor.adapters.database import DatabaseAdapter
from src.executor.adapters.alert import AlertAdapter

__all__ = [
    "ExecutionStatus",
    "ExecutionResult",
    "GitAdapter",
    "ContainerAdapter",
    "CIAdapter",
    "IaCAdapter",
    "DatabaseAdapter",
    "AlertAdapter",
]
