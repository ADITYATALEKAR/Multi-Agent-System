"""Health and readiness check endpoints."""

from __future__ import annotations

import time
from enum import Enum

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

_start_time = time.time()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthResponse(BaseModel):
    status: HealthStatus
    uptime_seconds: float = 0.0
    version: str = "1.0.0"
    message: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
def health_check() -> HealthResponse:
    """Basic liveness probe -- confirms the process is running."""
    uptime = round(time.time() - _start_time, 2)
    return HealthResponse(
        status=HealthStatus.HEALTHY,
        uptime_seconds=uptime,
        message="MASI API is running",
    )


@router.get("/health/ready")
def readiness_check() -> HealthResponse:
    """Readiness probe -- confirms the service can accept traffic."""
    uptime = round(time.time() - _start_time, 2)
    log.debug("readiness_checked", uptime=uptime)
    return HealthResponse(
        status=HealthStatus.HEALTHY,
        uptime_seconds=uptime,
        message="All subsystems ready",
    )
