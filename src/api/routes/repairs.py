"""Repair retrieval and approval endpoints."""

from __future__ import annotations

from enum import Enum

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.runtime import get_runtime

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["repairs"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RepairStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"


class Repair(BaseModel):
    repair_id: str
    task_id: str
    description: str = ""
    diff: str = ""
    status: RepairStatus = RepairStatus.PROPOSED
    rule: str = ""


class RepairsResponse(BaseModel):
    task_id: str
    repairs: list[Repair] = Field(default_factory=list)
    total: int = 0


class ApprovalResponse(BaseModel):
    repair_id: str
    status: RepairStatus
    message: str = ""


# ---------------------------------------------------------------------------
@router.get("/repairs/{task_id}")
def get_repairs(task_id: str) -> RepairsResponse:
    """Retrieve all proposed repairs for a given task."""
    runtime = get_runtime()
    items = runtime.get_repairs(task_id)
    log.info("repairs_listed", task_id=task_id, count=len(items))
    return RepairsResponse(
        task_id=task_id,
        repairs=[
            Repair(
                repair_id=item.repair_id,
                task_id=item.task_id,
                description=item.description,
                diff=item.diff,
                status=RepairStatus(item.status),
                rule=item.rule,
            )
            for item in items
        ],
        total=len(items),
    )


@router.post("/repairs/{repair_id}/approve")
def approve_repair(repair_id: str) -> ApprovalResponse:
    """Approve a proposed repair, marking it ready for application."""
    runtime = get_runtime()
    existing = runtime.get_repair(repair_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repair {repair_id} not found",
        )
    if existing.status != RepairStatus.PROPOSED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Repair {repair_id} is in state '{existing.status}', only 'proposed' repairs can be approved",
        )

    repair = runtime.approve_repair(repair_id)
    if repair is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repair {repair_id} not found",
        )
    log.info("repair_approved", repair_id=repair_id)
    return ApprovalResponse(
        repair_id=repair_id,
        status=RepairStatus.APPROVED,
        message="Repair approved successfully",
    )
