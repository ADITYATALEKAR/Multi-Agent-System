"""Base class and violation model for all IIE verification passes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from src.iie.architecture_ir import ArchitectureIR

log = structlog.get_logger(__name__)


class IntegrityViolation(BaseModel):
    """A single violation found by an IIE pass."""

    violation_id: str = Field(default_factory=lambda: str(uuid4()))
    pass_id: int
    pass_name: str
    severity: str  # "critical", "warning", "info"
    message: str
    component_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BasePass:
    """Abstract base for all IIE verification passes."""

    PASS_ID: int = 0
    PASS_NAME: str = ""

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        """Execute the verification pass against the given IR.

        Subclasses must override this method.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.run() not implemented")

    def _violation(
        self,
        severity: str,
        message: str,
        component_id: str | None = None,
        **details: Any,
    ) -> IntegrityViolation:
        """Helper to create a violation stamped with this pass's identity."""
        return IntegrityViolation(
            pass_id=self.PASS_ID,
            pass_name=self.PASS_NAME,
            severity=severity,
            message=message,
            component_id=component_id,
            details=details if details else {},
        )
