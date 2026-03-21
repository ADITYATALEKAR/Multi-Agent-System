"""Repair verification engine with 5 modalities.

Modalities:
1. StaticVerifier: type/constraint checks on repair actions
2. GraphLawVerifier: verify repair doesn't introduce new law violations
3. DynamicVerifier: sandbox-based dynamic testing (Docker)
4. RegressionChecker: check repair doesn't break previously passing checks
5. SecurityVerifier: check repair doesn't introduce security issues
"""

from __future__ import annotations

import enum
from typing import Any, Optional
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.repair.planner import RepairAction, RepairActionType, RepairTrajectory

logger = structlog.get_logger()


class VerificationStatus(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WARNING = "warning"


class VerificationCheck(BaseModel):
    """Result of a single verification check."""

    check_id: UUID = Field(default_factory=uuid4)
    modality: str
    status: VerificationStatus
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    """Aggregate result of all verification checks on a repair trajectory."""

    result_id: UUID = Field(default_factory=uuid4)
    trajectory_id: UUID
    overall_status: VerificationStatus = VerificationStatus.PASSED
    checks: list[VerificationCheck] = Field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    warning_count: int = 0

    def is_valid(self) -> bool:
        return self.overall_status in (VerificationStatus.PASSED, VerificationStatus.WARNING)


# ── Verification modalities ─────────────────────────────────────────────


class StaticVerifier:
    """Modality 1: Static type and constraint checks on repair actions."""

    MODALITY = "static"

    def verify(self, trajectory: RepairTrajectory) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []

        if not trajectory.actions:
            checks.append(
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.FAILED,
                    message="Empty repair trajectory — no actions to verify",
                )
            )
            return checks

        for action in trajectory.actions:
            check = self._verify_action(action)
            checks.append(check)

        return checks

    def _verify_action(self, action: RepairAction) -> VerificationCheck:
        # Check action has a valid target
        if action.target_entity is None:
            return VerificationCheck(
                modality=self.MODALITY,
                status=VerificationStatus.FAILED,
                message=f"Action {action.action_id} has no target entity",
            )

        # Type-specific checks
        if action.action_type == RepairActionType.ADD_EDGE:
            if "target_id" not in action.parameters:
                return VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.WARNING,
                    message=f"ADD_EDGE action {action.action_id} missing target_id parameter",
                )

        if action.action_type == RepairActionType.UPDATE_ATTRIBUTE:
            if "key" not in action.parameters:
                return VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.FAILED,
                    message=f"UPDATE_ATTRIBUTE action {action.action_id} missing 'key' parameter",
                )

        # High risk warning
        if action.risk > 0.8:
            return VerificationCheck(
                modality=self.MODALITY,
                status=VerificationStatus.WARNING,
                message=f"Action {action.action_id} has high risk ({action.risk:.2f})",
            )

        return VerificationCheck(
            modality=self.MODALITY,
            status=VerificationStatus.PASSED,
            message=f"Action {action.action_id} passes static checks",
        )


class GraphLawVerifier:
    """Modality 2: Verify repair doesn't introduce new law violations."""

    MODALITY = "graph_law"

    def verify(
        self,
        trajectory: RepairTrajectory,
        current_violations: set[UUID] | None = None,
        simulated_violations: set[UUID] | None = None,
    ) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        current = current_violations or set()
        simulated = simulated_violations or set()

        new_violations = simulated - current
        fixed_violations = current - simulated

        if new_violations:
            checks.append(
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.FAILED,
                    message=f"Repair introduces {len(new_violations)} new violations",
                    details={"new_violation_ids": [str(v) for v in new_violations]},
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.PASSED,
                    message=f"No new violations. {len(fixed_violations)} violations resolved.",
                    details={"fixed_count": len(fixed_violations)},
                )
            )

        return checks


class DynamicVerifier:
    """Modality 3: Sandbox-based dynamic testing.

    In production this would execute in Docker (rootless, 2 cores, 2GB RAM, 120s timeout).
    Here we provide the verification interface; actual Docker execution is optional.
    """

    MODALITY = "dynamic"

    def __init__(
        self,
        cpu_limit: int = 2,
        memory_limit_mb: int = 2048,
        timeout_seconds: int = 120,
    ) -> None:
        self._cpu_limit = cpu_limit
        self._memory_limit_mb = memory_limit_mb
        self._timeout = timeout_seconds

    def verify(
        self,
        trajectory: RepairTrajectory,
        test_results: dict[str, bool] | None = None,
    ) -> list[VerificationCheck]:
        """Verify trajectory using test results (simulated or real).

        Args:
            trajectory: The repair to verify.
            test_results: Dict of test_name -> passed. If None, skip dynamic verification.
        """
        if test_results is None:
            return [
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.SKIPPED,
                    message="Dynamic verification skipped — no test results provided",
                )
            ]

        checks: list[VerificationCheck] = []
        for test_name, passed in test_results.items():
            checks.append(
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
                    message=f"Test '{test_name}' {'passed' if passed else 'failed'}",
                    details={"test_name": test_name},
                )
            )

        return checks


class RegressionChecker:
    """Modality 4: Check repair doesn't break previously passing checks."""

    MODALITY = "regression"

    def verify(
        self,
        trajectory: RepairTrajectory,
        baseline_passing: set[str] | None = None,
        post_repair_passing: set[str] | None = None,
    ) -> list[VerificationCheck]:
        if baseline_passing is None or post_repair_passing is None:
            return [
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.SKIPPED,
                    message="Regression check skipped — no baseline/post-repair data",
                )
            ]

        regressions = baseline_passing - post_repair_passing
        if regressions:
            return [
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.FAILED,
                    message=f"Repair causes {len(regressions)} regressions",
                    details={"regressed_checks": list(regressions)},
                )
            ]

        return [
            VerificationCheck(
                modality=self.MODALITY,
                status=VerificationStatus.PASSED,
                message="No regressions detected",
            )
        ]


class SecurityVerifier:
    """Modality 5: Check repair doesn't introduce security issues."""

    MODALITY = "security"

    # Known dangerous patterns
    DANGEROUS_PATTERNS = {
        "remove_auth",
        "disable_tls",
        "allow_all",
        "bypass_validation",
        "skip_verification",
    }

    def verify(self, trajectory: RepairTrajectory) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []

        for action in trajectory.actions:
            # Check for dangerous parameter values
            for key, value in action.parameters.items():
                value_str = str(value).lower()
                for pattern in self.DANGEROUS_PATTERNS:
                    if pattern in value_str:
                        checks.append(
                            VerificationCheck(
                                modality=self.MODALITY,
                                status=VerificationStatus.FAILED,
                                message=f"Security risk: action {action.action_id} contains '{pattern}'",
                                details={"pattern": pattern, "key": key},
                            )
                        )

            # Check description for security keywords
            desc_lower = action.description.lower()
            for pattern in self.DANGEROUS_PATTERNS:
                if pattern in desc_lower:
                    checks.append(
                        VerificationCheck(
                            modality=self.MODALITY,
                            status=VerificationStatus.FAILED,
                            message=f"Security risk in description: '{pattern}'",
                        )
                    )

        if not checks:
            checks.append(
                VerificationCheck(
                    modality=self.MODALITY,
                    status=VerificationStatus.PASSED,
                    message="No security issues detected",
                )
            )

        return checks


# ── Main VerificationEngine ─────────────────────────────────────────────


class VerificationEngine:
    """Orchestrates all 5 verification modalities for a repair trajectory."""

    def __init__(self) -> None:
        self._static = StaticVerifier()
        self._graph_law = GraphLawVerifier()
        self._dynamic = DynamicVerifier()
        self._regression = RegressionChecker()
        self._security = SecurityVerifier()

    def verify(
        self,
        trajectory: RepairTrajectory,
        context: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Run all verification modalities and aggregate results."""
        context = context or {}
        all_checks: list[VerificationCheck] = []

        # 1. Static verification
        all_checks.extend(self._static.verify(trajectory))

        # 2. Graph law verification
        all_checks.extend(
            self._graph_law.verify(
                trajectory,
                current_violations=context.get("current_violations"),
                simulated_violations=context.get("simulated_violations"),
            )
        )

        # 3. Dynamic verification
        all_checks.extend(
            self._dynamic.verify(
                trajectory,
                test_results=context.get("test_results"),
            )
        )

        # 4. Regression check
        all_checks.extend(
            self._regression.verify(
                trajectory,
                baseline_passing=context.get("baseline_passing"),
                post_repair_passing=context.get("post_repair_passing"),
            )
        )

        # 5. Security verification
        all_checks.extend(self._security.verify(trajectory))

        # Aggregate
        passed = sum(1 for c in all_checks if c.status == VerificationStatus.PASSED)
        failed = sum(1 for c in all_checks if c.status == VerificationStatus.FAILED)
        warnings = sum(1 for c in all_checks if c.status == VerificationStatus.WARNING)

        if failed > 0:
            overall = VerificationStatus.FAILED
        elif warnings > 0:
            overall = VerificationStatus.WARNING
        else:
            overall = VerificationStatus.PASSED

        result = VerificationResult(
            trajectory_id=trajectory.trajectory_id,
            overall_status=overall,
            checks=all_checks,
            passed_count=passed,
            failed_count=failed,
            warning_count=warnings,
        )

        logger.debug(
            "verification_complete",
            trajectory_id=str(trajectory.trajectory_id),
            overall=overall.value,
            passed=passed,
            failed=failed,
            warnings=warnings,
        )

        return result
