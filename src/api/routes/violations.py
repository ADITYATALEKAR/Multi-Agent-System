"""Violation listing endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.runtime import get_runtime

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["violations"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Violation(BaseModel):
    violation_id: str
    rule: str
    severity: str = "medium"
    file_path: str = ""
    message: str = ""


class ViolationsResponse(BaseModel):
    tenant_id: str
    violations: list[Violation] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
@router.get("/violations")
def list_violations(tenant_id: str = "default") -> ViolationsResponse:
    """List all recorded violations for a tenant."""
    runtime = get_runtime()
    items = runtime.list_violations(tenant_id)
    log.info("violations_listed", tenant_id=tenant_id, count=len(items))
    return ViolationsResponse(
        tenant_id=tenant_id,
        violations=[
            Violation(
                violation_id=item.violation_id,
                rule=item.rule,
                severity=item.severity,
                file_path=item.file_path,
                message=item.message,
            )
            for item in items
        ],
        total=len(items),
    )
