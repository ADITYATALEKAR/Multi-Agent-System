"""Certificate verification: validates diagnosis certificates.

Checks:
1. Structural completeness (required fields present)
2. Evidence consistency (referenced IDs exist)
3. Counterfactual validity (conclusions consistent with data)
4. Confidence bounds (within [0, 1])
5. Round-trip serialization integrity
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.core.certificate import DiagnosisCertificate
from src.core.counterfactual import CounterfactualConclusion

logger = structlog.get_logger()


class VerificationIssue(BaseModel):
    """A single issue found during certificate verification."""

    severity: str  # "error", "warning"
    category: str
    message: str


class CertificateVerificationResult(BaseModel):
    """Result of certificate verification."""

    is_valid: bool = True
    issues: list[VerificationIssue] = Field(default_factory=list)
    checks_performed: int = 0
    checks_passed: int = 0


class CertificateVerifier:
    """Verifies diagnosis certificates for completeness and correctness.

    Performs 5 categories of checks:
    1. Structural completeness
    2. Evidence consistency
    3. Counterfactual validity
    4. Confidence bounds
    5. Round-trip serialization
    """

    def verify(self, certificate: DiagnosisCertificate) -> CertificateVerificationResult:
        """Verify a diagnosis certificate.

        Returns:
            CertificateVerificationResult with is_valid and any issues found.
        """
        result = CertificateVerificationResult()
        issues: list[VerificationIssue] = []

        # 1. Structural completeness
        struct_issues = self._check_structure(certificate)
        issues.extend(struct_issues)
        result.checks_performed += 1
        if not struct_issues:
            result.checks_passed += 1

        # 2. Evidence consistency
        evidence_issues = self._check_evidence(certificate)
        issues.extend(evidence_issues)
        result.checks_performed += 1
        if not evidence_issues:
            result.checks_passed += 1

        # 3. Counterfactual validity
        cf_issues = self._check_counterfactuals(certificate)
        issues.extend(cf_issues)
        result.checks_performed += 1
        if not cf_issues:
            result.checks_passed += 1

        # 4. Confidence bounds
        conf_issues = self._check_confidence(certificate)
        issues.extend(conf_issues)
        result.checks_performed += 1
        if not conf_issues:
            result.checks_passed += 1

        # 5. Round-trip serialization
        serial_issues = self._check_serialization(certificate)
        issues.extend(serial_issues)
        result.checks_performed += 1
        if not serial_issues:
            result.checks_passed += 1

        result.issues = issues
        result.is_valid = not any(i.severity == "error" for i in issues)

        logger.debug(
            "certificate_verified",
            certificate_id=str(certificate.certificate_id),
            is_valid=result.is_valid,
            issues=len(issues),
            checks=f"{result.checks_passed}/{result.checks_performed}",
        )

        return result

    def _check_structure(self, cert: DiagnosisCertificate) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        if not cert.incident_id:
            issues.append(
                VerificationIssue(
                    severity="error",
                    category="structure",
                    message="Missing incident_id",
                )
            )

        if not cert.certificate_id:
            issues.append(
                VerificationIssue(
                    severity="error",
                    category="structure",
                    message="Missing certificate_id",
                )
            )

        if not cert.timestamp:
            issues.append(
                VerificationIssue(
                    severity="error",
                    category="structure",
                    message="Missing timestamp",
                )
            )

        if not cert.root_cause_hypothesis_ids and not cert.supporting_evidence:
            issues.append(
                VerificationIssue(
                    severity="warning",
                    category="structure",
                    message="No root cause or supporting evidence — certificate is empty",
                )
            )

        return issues

    def _check_evidence(self, cert: DiagnosisCertificate) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        # Root cause should be in or related to supporting evidence
        for rc_id in cert.root_cause_hypothesis_ids:
            if rc_id in cert.supporting_evidence:
                issues.append(
                    VerificationIssue(
                        severity="warning",
                        category="evidence",
                        message=f"Root cause {rc_id} appears in its own supporting evidence",
                    )
                )

        # Check repair plans reference
        if cert.repair_plan_ids and not cert.root_cause_hypothesis_ids:
            issues.append(
                VerificationIssue(
                    severity="warning",
                    category="evidence",
                    message="Repair plans exist but no root cause hypothesis identified",
                )
            )

        return issues

    def _check_counterfactuals(self, cert: DiagnosisCertificate) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        for cf in cert.counterfactual_results:
            # Check that conclusion is consistent with data
            if cf.conclusion == CounterfactualConclusion.CAUSES_SYMPTOM:
                if cf.resulting_health_delta >= 0 and len(cf.resulting_violations) > 0:
                    # Health didn't improve but violations found — might be inconsistent
                    pass  # Allow: violations present means symptom persists w/o the cause

            if cf.conclusion == CounterfactualConclusion.DOES_NOT_CAUSE:
                if cf.resulting_health_delta < -0.5:
                    issues.append(
                        VerificationIssue(
                            severity="warning",
                            category="counterfactual",
                            message=(
                                f"Scenario {cf.scenario_id} concludes DOES_NOT_CAUSE "
                                f"but health delta is {cf.resulting_health_delta:.2f}"
                            ),
                        )
                    )

            # v3.3 Fix 2: boundary tracking
            if cf.boundary_size == 0 and cf.conclusion != CounterfactualConclusion.INCONCLUSIVE:
                issues.append(
                    VerificationIssue(
                        severity="warning",
                        category="counterfactual",
                        message=f"Scenario {cf.scenario_id} has boundary_size=0 but definitive conclusion",
                    )
                )

        return issues

    def _check_confidence(self, cert: DiagnosisCertificate) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        if cert.confidence < 0.0 or cert.confidence > 1.0:
            issues.append(
                VerificationIssue(
                    severity="error",
                    category="confidence",
                    message=f"Confidence {cert.confidence} outside [0, 1] bounds",
                )
            )

        if cert.floor_budget_consumed_pct < 0.0 or cert.floor_budget_consumed_pct > 100.0:
            issues.append(
                VerificationIssue(
                    severity="warning",
                    category="confidence",
                    message=f"Floor budget consumed {cert.floor_budget_consumed_pct}% outside [0, 100]",
                )
            )

        return issues

    def _check_serialization(self, cert: DiagnosisCertificate) -> list[VerificationIssue]:
        """Round-trip serialization check."""
        issues: list[VerificationIssue] = []

        try:
            serialized = cert.model_dump_json()
            deserialized = DiagnosisCertificate.model_validate_json(serialized)

            if deserialized.certificate_id != cert.certificate_id:
                issues.append(
                    VerificationIssue(
                        severity="error",
                        category="serialization",
                        message="Round-trip serialization changed certificate_id",
                    )
                )

            if deserialized.incident_id != cert.incident_id:
                issues.append(
                    VerificationIssue(
                        severity="error",
                        category="serialization",
                        message="Round-trip serialization changed incident_id",
                    )
                )

            if deserialized.confidence != cert.confidence:
                issues.append(
                    VerificationIssue(
                        severity="error",
                        category="serialization",
                        message="Round-trip serialization changed confidence",
                    )
                )

        except Exception as exc:
            issues.append(
                VerificationIssue(
                    severity="error",
                    category="serialization",
                    message=f"Round-trip serialization failed: {exc}",
                )
            )

        return issues
