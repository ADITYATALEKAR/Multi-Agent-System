"""In-process MASI runtime built on top of the coordination layer."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.coordination.agents import (
    CausalRCAAgent,
    ExecutorAgent,
    ExplainerAgent,
    HypothesisAgent,
    InfraOpsAgent,
    LawEngineAgent,
    MemoryAgent,
    RepairPlannerAgent,
    RepoMapperAgent,
    VerificationAgent,
)
from src.coordination.orchestrator import Orchestrator
from src.core.coordination import WorkItem, WorkItemStatus


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeViolation(BaseModel):
    violation_id: str
    rule: str
    severity: str = "medium"
    file_path: str = ""
    message: str = ""


class RuntimeHypothesis(BaseModel):
    title: str
    summary: str


class RuntimeRepair(BaseModel):
    repair_id: str
    task_id: str
    description: str = ""
    diff: str = ""
    status: str = "proposed"
    rule: str = ""


class RuntimeWorkItem(BaseModel):
    item_id: str
    task_type: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)


class RuntimeTask(BaseModel):
    task_id: str
    tenant_id: str
    repo_path: str
    status: str = "pending"
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)
    work_items: list[RuntimeWorkItem] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    violations: list[RuntimeViolation] = Field(default_factory=list)
    hypotheses: list[RuntimeHypothesis] = Field(default_factory=list)
    repairs: list[RuntimeRepair] = Field(default_factory=list)


class RuntimeState(BaseModel):
    tasks: dict[str, RuntimeTask] = Field(default_factory=dict)


class MASIRuntime:
    """High-level runtime that executes the MASI analysis pipeline."""

    def __init__(self, state_path: Path | None = None) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self._state_path = state_path or repo_root / ".masi_runtime" / "state.json"
        self._lock = threading.Lock()
        self._state = self._load_state()

    def submit_analysis(self, repo_path: str, tenant_id: str = "default") -> RuntimeTask:
        target = Path(repo_path).expanduser().resolve()
        task = RuntimeTask(
            task_id=self._next_task_id(),
            tenant_id=tenant_id,
            repo_path=str(target),
            status="running",
        )
        self._state.tasks[task.task_id] = task
        self._save_state()

        orchestrator = self._build_orchestrator()
        stages: list[RuntimeWorkItem] = []

        repo_stage = self._run_stage(
            orchestrator=orchestrator,
            task_type="repo_map",
            payload={"path": str(target)},
            required_capabilities={"repo_map"},
            tenant_id=tenant_id,
            priority=0.95,
        )
        stages.append(repo_stage)
        repo_summary = repo_stage.result.get("repo_summary", {})

        law_stage = self._run_stage(
            orchestrator=orchestrator,
            task_type="law_check",
            payload={"graph_context": repo_summary},
            required_capabilities={"law_check"},
            tenant_id=tenant_id,
            priority=0.85,
        )
        stages.append(law_stage)
        violations = [
            RuntimeViolation(
                violation_id=f"{task.task_id}_v_{idx}",
                rule=str(item.get("rule", "unknown_rule")),
                severity=str(item.get("severity", "medium")),
                file_path=str(item.get("file_path", "")),
                message=str(item.get("message", "")),
            )
            for idx, item in enumerate(law_stage.result.get("violations", []), start=1)
        ]

        hypothesis_stage = self._run_stage(
            orchestrator=orchestrator,
            task_type="hypothesis_generate",
            payload={"violations": [item.model_dump() for item in violations]},
            required_capabilities={"hypothesis_generate"},
            tenant_id=tenant_id,
            priority=0.75,
        )
        stages.append(hypothesis_stage)
        hypotheses = [
            RuntimeHypothesis(
                title=str(item.get("title", "Untitled hypothesis")),
                summary=str(item.get("summary", "")),
            )
            for item in hypothesis_stage.result.get("hypotheses", [])
        ]

        repair_stage = self._run_stage(
            orchestrator=orchestrator,
            task_type="repair_plan",
            payload={"violations": [item.model_dump() for item in violations]},
            required_capabilities={"repair_plan"},
            tenant_id=tenant_id,
            priority=0.65,
        )
        stages.append(repair_stage)
        repairs = [
            RuntimeRepair(
                repair_id=f"{task.task_id}_{item.get('candidate_id', f'repair_{idx}')}",
                task_id=task.task_id,
                description=str(item.get("description", "")),
                rule=str(item.get("rule", "")),
            )
            for idx, item in enumerate(repair_stage.result.get("repairs", []), start=1)
        ]

        explain_stage = self._run_stage(
            orchestrator=orchestrator,
            task_type="explain",
            payload={
                "context": {
                    "repo_summary": repo_summary,
                    "violations": [item.model_dump() for item in violations],
                    "hypotheses": [item.model_dump() for item in hypotheses],
                    "repairs": [item.model_dump() for item in repairs],
                }
            },
            required_capabilities={"explain"},
            tenant_id=tenant_id,
            priority=0.5,
        )
        stages.append(explain_stage)

        task.work_items = stages
        task.violations = violations
        task.hypotheses = hypotheses
        task.repairs = repairs
        task.status = "completed"
        task.updated_at = _utc_now()
        task.result = {
            "repo_summary": repo_summary,
            "violations_found": len(violations),
            "hypotheses_generated": len(hypotheses),
            "repairs_proposed": len(repairs),
            "explanation": explain_stage.result.get("explanation", ""),
        }
        self._state.tasks[task.task_id] = task
        self._save_state()
        return task

    def enqueue_analysis(self, repo_path: str, tenant_id: str = "default") -> RuntimeTask:
        """Persist a pending analysis task without executing it inline.

        The API uses this queued mode when clients explicitly provide
        scheduling metadata; it keeps the REST contract asynchronous while the
        CLI and local runtime can still use the synchronous submit path.
        """
        target = Path(repo_path).expanduser().resolve()
        task = RuntimeTask(
            task_id=self._next_task_id(),
            tenant_id=tenant_id,
            repo_path=str(target),
            status="pending",
        )
        self._state.tasks[task.task_id] = task
        self._save_state()
        return task

    def get_task(self, task_id: str) -> RuntimeTask | None:
        return self._state.tasks.get(task_id)

    def list_violations(self, tenant_id: str = "default") -> list[RuntimeViolation]:
        violations: list[RuntimeViolation] = []
        for task in self._state.tasks.values():
            if task.tenant_id == tenant_id:
                violations.extend(task.violations)
        return violations

    def get_repairs(self, task_id: str) -> list[RuntimeRepair]:
        task = self._state.tasks.get(task_id)
        return [] if task is None else list(task.repairs)

    def get_repair(self, repair_id: str) -> RuntimeRepair | None:
        for task in self._state.tasks.values():
            for repair in task.repairs:
                if repair.repair_id == repair_id:
                    return repair
        return None

    def approve_repair(self, repair_id: str) -> RuntimeRepair | None:
        repair = self.get_repair(repair_id)
        if repair is None:
            return None
        if repair.status == "proposed":
            repair.status = "approved"
            task = self._state.tasks.get(repair.task_id)
            if task is not None:
                task.updated_at = _utc_now()
            self._save_state()
        return repair

    def status(self) -> dict[str, Any]:
        completed = sum(1 for task in self._state.tasks.values() if task.status == "completed")
        running = sum(1 for task in self._state.tasks.values() if task.status == "running")
        return {
            "system": "healthy",
            "agents": 10,
            "triage": "inactive",
            "stored_tasks": len(self._state.tasks),
            "completed_tasks": completed,
            "running_tasks": running,
        }

    def health(self) -> dict[str, str]:
        return {"status": "healthy", "message": "MASI runtime is healthy"}

    def recent_tasks(self, limit: int = 20) -> list[RuntimeTask]:
        tasks = sorted(
            self._state.tasks.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return tasks[:limit]

    def _build_orchestrator(self) -> Orchestrator:
        orchestrator = Orchestrator()
        agents = [
            RepoMapperAgent(),
            LawEngineAgent(),
            HypothesisAgent(),
            CausalRCAAgent(),
            MemoryAgent(),
            RepairPlannerAgent(),
            VerificationAgent(),
            InfraOpsAgent(),
            ExplainerAgent(),
            ExecutorAgent(),
        ]
        for agent in agents:
            orchestrator.register_agent(agent)
        return orchestrator

    def _run_stage(
        self,
        orchestrator: Orchestrator,
        task_type: str,
        payload: dict[str, Any],
        required_capabilities: set[str],
        tenant_id: str,
        priority: float,
    ) -> RuntimeWorkItem:
        work_item = WorkItem(
            task_type=task_type,
            payload=payload,
            required_capabilities=required_capabilities,
            priority=priority,
        )
        task_id = orchestrator.submit_task([work_item], tenant_id=tenant_id)
        for _ in range(10):
            orchestrator.run_cycle()
            if orchestrator.check_termination(task_id):
                break

        items = orchestrator.get_task_items(task_id)
        item = items[0] if items else work_item
        result: dict[str, Any]
        if isinstance(item.result, dict):
            result = item.result
        elif item.result is None:
            result = {}
        else:
            result = {"value": item.result}
        return RuntimeWorkItem(
            item_id=str(item.item_id),
            task_type=item.task_type,
            status=item.status.value
            if isinstance(item.status, WorkItemStatus)
            else str(item.status),
            result=result,
        )

    def _load_state(self) -> RuntimeState:
        if not self._state_path.exists():
            return RuntimeState()
        with self._lock:
            raw = self._state_path.read_text(encoding="utf-8")
        if not raw.strip():
            return RuntimeState()
        return RuntimeState.model_validate_json(raw)

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._state_path.write_text(
                self._state.model_dump_json(indent=2),
                encoding="utf-8",
            )

    def _next_task_id(self) -> str:
        return f"task_{len(self._state.tasks) + 1:04d}"


_runtime_singleton: MASIRuntime | None = None


def get_runtime() -> MASIRuntime:
    global _runtime_singleton
    if _runtime_singleton is None:
        _runtime_singleton = MASIRuntime()
    return _runtime_singleton
