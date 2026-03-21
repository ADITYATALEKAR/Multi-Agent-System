"""RepairPlannerAgent — generates repair trajectories.

Uses src.repair.planner.RepairPlanner when available; falls back to stub.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class RepairPlannerAgent(BaseAgent):
    """Specialist agent for repair planning, trajectory generation, and repair scoring."""

    AGENT_ID: str = "repair_planner"
    CAPABILITIES: set[str] = {"repair_plan", "trajectory_generate", "repair_score"}

    def execute(self, item: WorkItem) -> Any:
        """Generate repair trajectories from root causes in the payload.

        Tries the real RepairPlanner; returns stub on ImportError.
        """
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

            planner = RepairPlanner()
            trajectories = planner.plan(root_causes=root_causes)
            count = len(trajectories) if trajectories else 0
            logger.info("repair_planner.planned", trajectories_generated=count)
            return {"trajectories_generated": count}
        except (ImportError, Exception) as exc:
            logger.warning("repair_planner.fallback", reason=str(exc))
            repairs: list[dict[str, str]] = []
            candidates = root_causes or violations
            for idx, cause in enumerate(candidates, start=1):
                rule = str(cause.get("rule", f"candidate_{idx}")) if isinstance(cause, dict) else f"candidate_{idx}"
                description = self._plan_description(rule)
                repairs.append({
                    "candidate_id": f"repair_{idx}",
                    "rule": rule,
                    "description": description,
                })
            return {
                "trajectories_generated": len(repairs),
                "repairs": repairs,
            }

    # ------------------------------------------------------------------
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
