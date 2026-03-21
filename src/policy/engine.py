"""Policy evaluation engine with dual mode: YAML rules + OPA/Rego.

Evaluates repair actions against policy before execution. Supports:
- evaluate(): makes binding decision
- simulate(): dry-run without side effects
- Dual-mode: YAML rules checked first, then OPA for deeper validation
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.policy.opa import OPAInput, OPAIntegration, OPAResult
from src.policy.yaml_rules import PolicyRule, YAMLRuleEngine

logger = structlog.get_logger()


class PolicyDecisionType(str, enum.Enum):
    APPROVE = "approve"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyDecision(BaseModel):
    """Result of policy evaluation."""

    decision_id: UUID = Field(default_factory=uuid4)
    decision: PolicyDecisionType = PolicyDecisionType.APPROVE
    reason: str = ""
    violations: list[str] = Field(default_factory=list)
    simulated: bool = False
    yaml_effect: str = ""
    opa_allowed: bool = True
    require_human_approval: bool = False


class PolicyEngine:
    """Dual-mode policy engine: YAML rules + OPA/Rego.

    Evaluation flow:
    1. YAML rules: fast, local rule checks
    2. OPA: deeper policy evaluation (if YAML allows)
    3. Final decision = intersection of both
    """

    def __init__(
        self,
        yaml_engine: YAMLRuleEngine | None = None,
        opa: OPAIntegration | None = None,
    ) -> None:
        self._yaml = yaml_engine or YAMLRuleEngine()
        self._opa = opa or OPAIntegration()

    def load_yaml_rules(self, rules_data: list[dict[str, Any]]) -> None:
        """Load YAML rules from parsed data."""
        self._yaml.load_rules(rules_data)

    def add_yaml_rule(self, rule: PolicyRule) -> None:
        """Add a single YAML rule."""
        self._yaml.add_rule(rule)

    def evaluate(
        self,
        action_type: str,
        environment: str,
        risk: float,
        confidence: float,
        agent_id: str = "",
        tenant_id: str = "default",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Evaluate an action against all policy layers.

        Returns:
            PolicyDecision with approve/deny/require_approval.
        """
        return self._run_evaluation(
            action_type=action_type,
            environment=environment,
            risk=risk,
            confidence=confidence,
            agent_id=agent_id,
            tenant_id=tenant_id,
            context=context,
            simulated=False,
        )

    def simulate(
        self,
        action_type: str,
        environment: str,
        risk: float,
        confidence: float,
        agent_id: str = "",
        tenant_id: str = "default",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Dry-run evaluation — no side effects.

        Returns:
            PolicyDecision with simulated=True.
        """
        return self._run_evaluation(
            action_type=action_type,
            environment=environment,
            risk=risk,
            confidence=confidence,
            agent_id=agent_id,
            tenant_id=tenant_id,
            context=context,
            simulated=True,
        )

    def _run_evaluation(
        self,
        action_type: str,
        environment: str,
        risk: float,
        confidence: float,
        agent_id: str,
        tenant_id: str,
        context: dict[str, Any] | None,
        simulated: bool,
    ) -> PolicyDecision:
        """Core evaluation logic shared by evaluate() and simulate()."""
        violations: list[str] = []

        # 1. YAML rules
        yaml_effect, yaml_evals = self._yaml.evaluate(
            action_type=action_type,
            environment=environment,
            risk=risk,
            confidence=confidence,
            context=context,
        )

        if yaml_effect == "deny":
            reasons = [e.reason for e in yaml_evals if e.matched]
            violations.extend(reasons)

        # 2. OPA evaluation
        opa_input = OPAInput(
            action_type=action_type,
            environment=environment,
            risk=risk,
            confidence=confidence,
            agent_id=agent_id,
            tenant_id=tenant_id,
            context=context or {},
        )
        opa_result = self._opa.evaluate(opa_input)

        if not opa_result.allowed:
            violations.extend(opa_result.violations)

        # 3. Final decision
        require_approval = opa_result.require_approval
        if yaml_effect == "deny" or not opa_result.allowed:
            decision_type = PolicyDecisionType.DENY
        elif require_approval:
            decision_type = PolicyDecisionType.REQUIRE_APPROVAL
        else:
            decision_type = PolicyDecisionType.APPROVE

        reason = "; ".join(violations) if violations else "all policies passed"

        decision = PolicyDecision(
            decision=decision_type,
            reason=reason,
            violations=violations,
            simulated=simulated,
            yaml_effect=yaml_effect,
            opa_allowed=opa_result.allowed,
            require_human_approval=require_approval,
        )

        logger.info(
            "policy_engine.decision",
            decision=decision_type.value,
            action_type=action_type,
            environment=environment,
            simulated=simulated,
            violations=len(violations),
        )

        return decision
