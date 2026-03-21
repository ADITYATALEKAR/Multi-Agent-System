"""Task submission and retrieval endpoints."""

from __future__ import annotations

from enum import StrEnum

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.runtime import get_runtime

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["tasks"])


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TaskSubmitRequest(BaseModel):
    task_type: str = Field(..., description="Type of analysis task to run")
    priority: TaskPriority = TaskPriority.MEDIUM
    scope: str = Field(default="full", description="Scope of the analysis")
    repo_path: str = Field(default=".", description="Path to the repository to analyze")
    tenant_id: str = "default"


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: TaskStatus


class WorkItem(BaseModel):
    item_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING


class TaskViolation(BaseModel):
    violation_id: str
    rule: str
    severity: str
    file_path: str = ""
    message: str = ""


class TaskHypothesis(BaseModel):
    title: str
    summary: str


class TaskRepair(BaseModel):
    repair_id: str
    status: str
    description: str = ""
    rule: str = ""


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    work_items: list[WorkItem] = Field(default_factory=list)
    result: dict | None = None
    created_at: str = ""
    repo_path: str = ""
    violations: list[TaskViolation] = Field(default_factory=list)
    hypotheses: list[TaskHypothesis] = Field(default_factory=list)
    repairs: list[TaskRepair] = Field(default_factory=list)


def _serialize_task(task) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        status=_map_runtime_status(task.status),
        work_items=[
            WorkItem(
                item_id=item.item_id,
                description=item.task_type,
                status=_map_runtime_status(item.status),
            )
            for item in task.work_items
        ],
        result=task.result,
        created_at=task.created_at,
        repo_path=task.repo_path,
        violations=[
            TaskViolation(
                violation_id=item.violation_id,
                rule=item.rule,
                severity=item.severity,
                file_path=item.file_path,
                message=item.message,
            )
            for item in task.violations
        ],
        hypotheses=[
            TaskHypothesis(
                title=item.title,
                summary=item.summary,
            )
            for item in task.hypotheses
        ],
        repairs=[
            TaskRepair(
                repair_id=item.repair_id,
                status=item.status,
                description=item.description,
                rule=item.rule,
            )
            for item in task.repairs
        ],
    )


def _map_runtime_status(status: str) -> TaskStatus:
    if status in {"completed", "complete"}:
        return TaskStatus.COMPLETED
    if status == "running":
        return TaskStatus.RUNNING
    if status == "failed":
        return TaskStatus.FAILED
    return TaskStatus.PENDING


# ---------------------------------------------------------------------------
@router.post("/tasks", status_code=201)
def submit_task(request: TaskSubmitRequest) -> TaskSubmitResponse:
    """Submit a new analysis task."""
    runtime = get_runtime()
    if request.task_type not in {"analysis", "repo_map"}:
        raise HTTPException(status_code=400, detail=f"Unsupported task type: {request.task_type}")
    explicit_scheduler_fields = request.model_fields_set - {"task_type"}
    if explicit_scheduler_fields:
        task = runtime.enqueue_analysis(request.repo_path, tenant_id=request.tenant_id)
    else:
        task = runtime.submit_analysis(request.repo_path, tenant_id=request.tenant_id)

    log.info(
        "task_submitted",
        task_id=task.task_id,
        task_type=request.task_type,
        priority=request.priority,
        tenant_id=request.tenant_id,
    )
    return TaskSubmitResponse(task_id=task.task_id, status=_map_runtime_status(task.status))


@router.get("/tasks")
def list_tasks(limit: int = 20) -> list[TaskResponse]:
    """List recent tasks from the runtime."""
    runtime = get_runtime()
    tasks = runtime.recent_tasks(limit=limit)
    return [_serialize_task(task) for task in tasks]


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> TaskResponse:
    """Retrieve the current state of a task."""
    runtime = get_runtime()
    task = runtime.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _serialize_task(task)
