"""LawEngineAgent — evaluates architectural / design laws.

Uses src.law.engine.LawEngine when available; falls back to stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class LawEngineAgent(BaseAgent):
    """Specialist agent for law-check, violation detection, and compliance scanning."""

    AGENT_ID: str = "law_engine"
    CAPABILITIES: set[str] = {"law_check", "violation_detect", "compliance_scan"}

    def execute(self, item: WorkItem) -> Any:
        """Evaluate laws against graph context from item payload.

        Tries the real LawEngine component; returns stub on ImportError.
        """
        logger.info(
            "law_engine.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        graph_context = payload.get("graph_context") if isinstance(payload, dict) else None

        try:
            from src.law.engine import LawEngine

            engine = LawEngine()
            violations = engine.evaluate(graph_context) if graph_context else []
            count = len(violations) if violations else 0
            logger.info("law_engine.evaluated", violations_found=count)
            return {"violations_found": count}
        except (ImportError, Exception) as exc:
            logger.warning("law_engine.fallback", reason=str(exc))
            return self._fallback_evaluate(payload)

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 1.5

    def _fallback_evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        graph_context = payload.get("graph_context") if isinstance(payload, dict) else None
        repo_summary = graph_context if isinstance(graph_context, dict) else {}
        repo_path = Path(str(repo_summary.get("path", "."))).expanduser()

        violations: list[dict[str, str]] = []
        if not repo_summary.get("exists", True):
            violations.append({
                "rule": "repository.exists",
                "severity": "high",
                "file_path": str(repo_path),
                "message": "The target repository path does not exist.",
            })
        else:
            if not (repo_path / "README.md").exists():
                violations.append({
                    "rule": "repo.documentation.readme",
                    "severity": "medium",
                    "file_path": str(repo_path / "README.md"),
                    "message": "Repository is missing a README.md file.",
                })

            tests_dir = repo_path / "tests"
            if not tests_dir.exists():
                violations.append({
                    "rule": "repo.quality.tests",
                    "severity": "medium",
                    "file_path": str(tests_dir),
                    "message": "Repository does not contain a tests directory.",
                })

            if (repo_path / "node_modules").exists():
                violations.append({
                    "rule": "repo.dependencies.vendored",
                    "severity": "high",
                    "file_path": str(repo_path / "node_modules"),
                    "message": "Vendored node_modules directory detected in repository root.",
                })

        return {
            "violations_found": len(violations),
            "violations": violations,
        }
