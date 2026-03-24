"""RepairPlannerAgent generates repair trajectories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.derived import DerivedFact, DerivedStatus, DerivedType, ExtendedJustification

if TYPE_CHECKING:
    from src.core.coordination import WorkItem
    from src.repair.planner import RepairTrajectory

logger = structlog.get_logger(__name__)


class RepairPlannerAgent(BaseAgent):
    """Specialist agent for repair planning, trajectory generation, and repair scoring."""

    AGENT_ID: str = "repair_planner"
    CAPABILITIES: set[str] = {"repair_plan", "trajectory_generate", "repair_score"}

    def execute(self, item: WorkItem) -> Any:
        """Generate repair trajectories from root causes in the payload."""
        logger.info(
            "repair_planner.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        root_causes = payload.get("root_causes", []) if isinstance(payload, dict) else []
        violations = payload.get("violations", []) if isinstance(payload, dict) else []

        try:
            from src.repair.planner import RepairPlanner

            candidate_inputs = root_causes or violations
            derived_violations = [
                self._to_derived_violation(candidate)
                for candidate in candidate_inputs
                if isinstance(candidate, dict)
            ]
            if not derived_violations:
                raise ValueError("No structured violations available for repair planning.")

            planner = RepairPlanner()
            trajectories = planner.score_candidates(
                planner.generate_candidates(
                    violations=derived_violations,
                    context={"root_causes": root_causes},
                )
            )
            count = len(trajectories) if trajectories else 0
            logger.info("repair_planner.planned", trajectories_generated=count)
            repairs = [
                self._serialize_trajectory(idx, trajectory, derived_violations)
                for idx, trajectory in enumerate(trajectories[:10], start=1)
            ]
            return {"trajectories_generated": count, "repairs": repairs}
        except (ImportError, Exception) as exc:
            logger.warning("repair_planner.fallback", reason=str(exc))
            repairs: list[dict[str, str]] = []
            candidates = root_causes or violations
            for idx, cause in enumerate(candidates, start=1):
                rule = (
                    str(cause.get("rule", f"candidate_{idx}"))
                    if isinstance(cause, dict)
                    else f"candidate_{idx}"
                )
                description = self._plan_description(rule)
                repairs.append(
                    {
                        "candidate_id": f"repair_{idx}",
                        "rule": rule,
                        "description": description,
                    }
                )
            return {
                "trajectories_generated": len(repairs),
                "repairs": repairs,
            }

    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 4.0

    def _plan_description(self, rule: str) -> str:
        if "readme" in rule:
            return "Create a README.md with setup, architecture, and run instructions."
        if "tests" in rule:
            return "Add a smoke test suite covering the main execution path."
        if "vendored" in rule:
            return "Remove vendored dependencies from source control and add ignore rules."
        return f"Investigate and remediate rule violation: {rule}."

    def _to_derived_violation(self, violation: dict[str, Any]) -> DerivedFact:
        rule = str(violation.get("rule", "unknown_rule"))
        file_path = str(violation.get("file_path", ""))
        entity_id = str(violation.get("entity_id") or uuid5(NAMESPACE_URL, file_path or rule))
        payload = {
            "rule_id": rule,
            "entity_id": entity_id,
            "message": str(violation.get("message", "")),
            "file_path": file_path,
            "severity": str(violation.get("severity", "medium")),
        }
        return DerivedFact(
            derived_type=DerivedType.VIOLATION,
            payload=payload,
            justification=ExtendedJustification(
                rule_id=rule,
                source_strategy="law_engine_agent",
            ),
            status=DerivedStatus.SUPPORTED,
            confidence=0.8,
        )

    def _serialize_trajectory(
        self,
        index: int,
        trajectory: RepairTrajectory,
        violations: list[DerivedFact],
    ) -> dict[str, str]:
        rule_lookup = {
            str(item.derived_id): str(item.payload.get("rule_id", ""))
            for item in violations
        }
        first_rule = ""
        for violation_id in trajectory.violation_ids:
            rule = rule_lookup.get(str(violation_id), "")
            if rule:
                first_rule = rule
                break
        descriptions = [action.description for action in trajectory.actions if action.description]
        description = "; ".join(descriptions[:3]).strip()
        if not description:
            description = (
                "Repair trajectory generated by "
                f"{trajectory.strategy or 'repair planner'}."
            )
        return {
            "candidate_id": str(getattr(trajectory, "trajectory_id", f"repair_{index}")),
            "rule": first_rule,
            "description": description,
        }
