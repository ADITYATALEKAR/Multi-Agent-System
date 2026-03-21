"""Certificate generation from diagnostic results.

Generates machine-checkable, independently verifiable DiagnosisCertificates
that capture the full audit trail of a diagnostic investigation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.core.certificate import (
    DiagnosisCertificate,
    OSGSubgraph,
    ParameterChange,
    SolverResult,
)
from src.core.counterfactual import CounterfactualScenario
from src.core.derived import DerivedFact

logger = structlog.get_logger()


class CertificateGenerator:
    """Generates diagnosis certificates from investigation results.

    Produces a complete audit trail including:
    - Root cause hypothesis IDs and supporting evidence
    - Counterfactual validation results
    - Solver results
    - OSG snapshot
    - Repair plan references
    - Self-improvement parameter changes
    - Law health states
    """

    def generate(
        self,
        investigation_id: UUID,
        violations: list[DerivedFact] | None = None,
        root_cause: DerivedFact | None = None,
        repair_plan_ids: list[UUID] | None = None,
        verification_passed: bool | None = None,
        counterfactuals: list[CounterfactualScenario] | None = None,
        solver_results: list[SolverResult] | None = None,
        attention_scores: dict[str, float] | None = None,
        osg_snapshot: OSGSubgraph | None = None,
        law_health_states: dict[str, str] | None = None,
        self_improvement_updates: list[ParameterChange] | None = None,
        budget_gated_operations: list[str] | None = None,
        mandatory_ops: list[str] | None = None,
        floor_budget_pct: float = 0.0,
    ) -> DiagnosisCertificate:
        """Generate a complete diagnosis certificate.

        Args:
            investigation_id: The incident being diagnosed.
            violations: All violations detected.
            root_cause: The identified root cause hypothesis.
            repair_plan_ids: IDs of generated repair plans.
            verification_passed: Whether verification passed.
            counterfactuals: Counterfactual simulation results.
            solver_results: Formal solver results.
            attention_scores: Attention scores at diagnosis time.
            osg_snapshot: OSG state snapshot.
            law_health_states: Current health state of each law.
            self_improvement_updates: Parameter changes from self-improvement.
            budget_gated_operations: Operations gated by cost-aware budget.
            mandatory_ops: Mandatory operations executed (v3.3 Fix 4).
            floor_budget_pct: Floor budget consumed percentage (v3.3 Fix 4).

        Returns:
            Complete DiagnosisCertificate.
        """
        violations = violations or []
        counterfactuals = counterfactuals or []

        # Build root cause hypothesis IDs
        root_cause_ids: list[UUID] = []
        if root_cause:
            root_cause_ids.append(root_cause.derived_id)

        # Build supporting evidence from violations
        evidence_ids: list[UUID] = [v.derived_id for v in violations]

        # Compute confidence
        confidence = self._compute_confidence(root_cause, counterfactuals, violations)

        # Simulation boundary sizes from counterfactuals
        boundary_sizes = [cf.boundary_size for cf in counterfactuals]

        cert = DiagnosisCertificate(
            incident_id=investigation_id,
            root_cause_hypothesis_ids=root_cause_ids,
            supporting_evidence=evidence_ids,
            confidence=confidence,
            counterfactual_results=counterfactuals,
            solver_results=solver_results or [],
            attention_scores_at_diagnosis=attention_scores or {},
            osg_snapshot=osg_snapshot or OSGSubgraph(),
            repair_plan_ids=repair_plan_ids or [],
            budget_gated_operations=budget_gated_operations or [],
            self_improvement_updates=self_improvement_updates or [],
            simulation_boundary_sizes=boundary_sizes,
            temporal_index_queries=len(counterfactuals),
            law_health_states=law_health_states or {},
            mandatory_ops_executed=mandatory_ops or [],
            floor_budget_consumed_pct=floor_budget_pct,
        )

        logger.info(
            "certificate_generated",
            certificate_id=str(cert.certificate_id),
            incident_id=str(investigation_id),
            confidence=confidence,
            violations=len(violations),
            counterfactuals=len(counterfactuals),
        )

        return cert

    def _compute_confidence(
        self,
        root_cause: DerivedFact | None,
        counterfactuals: list[CounterfactualScenario],
        violations: list[DerivedFact],
    ) -> float:
        """Compute overall certificate confidence.

        Factors:
        - Root cause confidence (40%)
        - Counterfactual support (35%)
        - Evidence coverage (25%)
        """
        # Root cause confidence
        rc_conf = root_cause.confidence if root_cause else 0.0

        # Counterfactual support
        from src.core.counterfactual import CounterfactualConclusion

        if counterfactuals:
            supporting = sum(
                1 for cf in counterfactuals
                if cf.conclusion == CounterfactualConclusion.CAUSES_SYMPTOM
            )
            cf_conf = supporting / len(counterfactuals)
        else:
            cf_conf = 0.0

        # Evidence coverage (more violations = more evidence, up to a point)
        evidence_conf = min(1.0, len(violations) / 5.0) if violations else 0.0

        total = 0.4 * rc_conf + 0.35 * cf_conf + 0.25 * evidence_conf
        return round(min(1.0, total), 4)
