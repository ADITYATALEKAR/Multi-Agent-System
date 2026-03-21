"""Open Policy Agent (OPA) / Rego integration.

Provides a local Rego-style evaluator for policy decisions when
an external OPA server is not available. Falls back gracefully.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class OPAInput(BaseModel):
    """Input payload for OPA policy evaluation."""

    action_type: str
    environment: str
    risk: float
    confidence: float
    agent_id: str = ""
    tenant_id: str = "default"
    context: dict[str, Any] = Field(default_factory=dict)


class OPAResult(BaseModel):
    """Result from OPA policy evaluation."""

    allowed: bool = True
    reason: str = ""
    violations: list[str] = Field(default_factory=list)
    require_approval: bool = False


class OPAIntegration:
    """OPA/Rego policy evaluator.

    In production, delegates to an OPA server via HTTP.
    For local/test use, implements core Rego-style rules in Python.
    """

    def __init__(self, opa_url: str | None = None) -> None:
        self._opa_url = opa_url
        self._local_policies: dict[str, dict[str, Any]] = {}
        self._default_policy = self._build_default_policy()

    def register_policy(self, name: str, policy: dict[str, Any]) -> None:
        """Register a named policy for local evaluation."""
        self._local_policies[name] = policy
        logger.info("opa.policy_registered", name=name)

    def evaluate(self, input_data: OPAInput, policy_name: str = "default") -> OPAResult:
        """Evaluate input against a policy.

        Uses local evaluation. In production, would call OPA server.
        """
        if self._opa_url:
            # In production, HTTP call to OPA server
            return self._evaluate_remote(input_data, policy_name)

        return self._evaluate_local(input_data, policy_name)

    def _evaluate_remote(self, input_data: OPAInput, policy_name: str) -> OPAResult:
        """Evaluate via remote OPA server (stub — real impl uses httpx)."""
        logger.info("opa.remote_eval", url=self._opa_url, policy=policy_name)
        # In production: POST to {opa_url}/v1/data/{policy_name}
        # For now, fall back to local
        return self._evaluate_local(input_data, policy_name)

    def _evaluate_local(self, input_data: OPAInput, policy_name: str) -> OPAResult:
        """Evaluate using local Python-based Rego rules."""
        policy = self._local_policies.get(policy_name, self._default_policy)
        violations: list[str] = []
        require_approval = False

        # Rule: environment restrictions
        blocked_envs = policy.get("blocked_environments", [])
        if input_data.environment in blocked_envs:
            violations.append(f"environment '{input_data.environment}' is blocked")

        # Rule: risk threshold
        max_risk = policy.get("max_risk", 0.8)
        if input_data.risk > max_risk:
            violations.append(f"risk {input_data.risk:.2f} exceeds threshold {max_risk:.2f}")

        # Rule: confidence floor
        min_confidence = policy.get("min_confidence", 0.3)
        if input_data.confidence < min_confidence:
            violations.append(f"confidence {input_data.confidence:.2f} below floor {min_confidence:.2f}")

        # Rule: production requires approval
        approval_envs = policy.get("approval_required_environments", ["production"])
        if input_data.environment in approval_envs:
            require_approval = True

        # Rule: action type restrictions
        blocked_actions = policy.get("blocked_action_types", [])
        if input_data.action_type in blocked_actions:
            violations.append(f"action_type '{input_data.action_type}' is blocked")

        allowed = len(violations) == 0
        reason = "; ".join(violations) if violations else "all checks passed"

        logger.debug(
            "opa.local_eval",
            policy=policy_name,
            allowed=allowed,
            violations=len(violations),
        )

        return OPAResult(
            allowed=allowed,
            reason=reason,
            violations=violations,
            require_approval=require_approval,
        )

    def _build_default_policy(self) -> dict[str, Any]:
        """Default production-safe policy."""
        return {
            "blocked_environments": [],
            "max_risk": 0.8,
            "min_confidence": 0.3,
            "approval_required_environments": ["production"],
            "blocked_action_types": [],
        }
