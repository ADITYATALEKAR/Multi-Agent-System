"""Backend chat reasoning over MAS runtime state and analysis results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.service import MASIRuntime, RuntimeTask


@dataclass(slots=True)
class RuntimeChatReply:
    """Structured response returned by the backend chat service."""

    answer: str
    intent: str = "answer"
    recommended_action: str | None = None
    source_task_id: str | None = None
    highlights: list[str] = field(default_factory=list)
    cards: list[RuntimeChatCard] = field(default_factory=list)
    follow_up_actions: list[RuntimeChatAction] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeChatCard:
    """Small structured card for compartmentalized UI rendering."""

    title: str
    body: str
    action: str | None = None
    action_label: str | None = None


@dataclass(slots=True)
class RuntimeChatAction:
    """Clickable follow-up action surfaced to the client."""

    action: str
    label: str


class RuntimeChatService:
    """Answer operator questions by reasoning over runtime state."""

    def __init__(self, runtime: MASIRuntime) -> None:
        self._runtime = runtime

    def answer(
        self,
        prompt: str,
        tenant_id: str = "default",
        task_id: str | None = None,
        repo_path: str | None = None,
    ) -> RuntimeChatReply:
        normalized = prompt.lower().strip()
        selected_task = self._select_task(tenant_id=tenant_id, task_id=task_id, repo_path=repo_path)
        recent_tasks = self._recent_tasks(tenant_id=tenant_id, limit=3)

        action_reply = self._detect_action_intent(normalized, selected_task)
        if action_reply is not None:
            return action_reply

        if "health" in normalized or "status" in normalized:
            return self._answer_status(recent_tasks)

        if any(
            token in normalized
            for token in {"latest task", "last task", "recent task", "recent analysis"}
        ):
            return self._answer_recent_tasks(recent_tasks)

        if any(token in normalized for token in {"summary", "summarize", "explain", "overview"}):
            return self._answer_summary(selected_task)

        if "violation" in normalized:
            return self._answer_violations(selected_task)

        if "repair" in normalized:
            return self._answer_repairs(selected_task)

        if "hypoth" in normalized:
            return self._answer_hypotheses(selected_task)

        if any(token in normalized for token in {"repo", "path", "workspace"}) and repo_path:
            return RuntimeChatReply(
                answer=f"I am analyzing this workspace path: {repo_path}",
                highlights=[repo_path],
            )

        return self._answer_default(selected_task, recent_tasks)

    def _detect_action_intent(
        self,
        normalized_prompt: str,
        selected_task: RuntimeTask | None,
    ) -> RuntimeChatReply | None:
        if "install" in normalized_prompt and (
            "runtime" in normalized_prompt or "setup" in normalized_prompt
        ):
            return RuntimeChatReply(
                answer=(
                    "I should install the MAS runtime before we can run the local"
                    " API and analysis pipeline."
                ),
                intent="action",
                recommended_action="installRuntime",
                follow_up_actions=[
                    RuntimeChatAction(action="installRuntime", label="setup"),
                ],
            )
        if (
            ("start" in normalized_prompt or "launch" in normalized_prompt)
            and "api" in normalized_prompt
        ):
            return RuntimeChatReply(
                answer=(
                    "I should start the MAS API so the sidebar can query tasks,"
                    " health, and analysis results."
                ),
                intent="action",
                recommended_action="startApi",
                follow_up_actions=[
                    RuntimeChatAction(action="startApi", label="start api"),
                ],
            )
        if "health" in normalized_prompt and "check" in normalized_prompt:
            return RuntimeChatReply(
                answer="I should run a live MAS health check against the local API.",
                intent="action",
                recommended_action="healthCheck",
                follow_up_actions=[
                    RuntimeChatAction(action="healthCheck", label="health"),
                ],
            )
        if any(token in normalized_prompt for token in {"analyze", "scan", "inspect"}) and any(
            token in normalized_prompt for token in {"workspace", "repo", "repository"}
        ):
            answer = "I should run MAS analysis for the current workspace."
            if selected_task is not None:
                answer += (
                    f" The latest task I know about is {selected_task.task_id},"
                    " so a fresh run will update that picture."
                )
            return RuntimeChatReply(
                answer=answer,
                intent="action",
                recommended_action="analyzeWorkspace",
                source_task_id=selected_task.task_id if selected_task is not None else None,
                follow_up_actions=[
                    RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
                    RuntimeChatAction(action="showLastTask", label="last task"),
                ],
            )
        if any(
            token in normalized_prompt
            for token in {"show last task", "open last task", "show latest task"}
        ):
            return RuntimeChatReply(
                answer="I should open the latest MAS task report.",
                intent="action",
                recommended_action="showLastTask",
                source_task_id=selected_task.task_id if selected_task is not None else None,
                follow_up_actions=[
                    RuntimeChatAction(action="showLastTask", label="open task"),
                ],
            )
        if any(
            token in normalized_prompt
            for token in {"llm", "api key", "provider", "chatgpt", "claude", "deepseek", "kimi"}
        ):
            return RuntimeChatReply(
                answer=(
                    "I should open the LLM connection flow so you can paste an API key,"
                    " choose a model, and then type plain-English instructions."
                ),
                intent="action",
                recommended_action="configureProvider",
                follow_up_actions=[
                    RuntimeChatAction(action="configureProvider", label="connect llm"),
                ],
            )
        return None

    def _answer_status(self, recent_tasks: list[RuntimeTask]) -> RuntimeChatReply:
        health = self._runtime.health()
        status = self._runtime.status()
        latest = recent_tasks[0] if recent_tasks else None
        latest_line = (
            f" Latest task: {latest.task_id} ({latest.status}) for {latest.repo_path}."
            if latest is not None
            else " No tasks have been recorded yet."
        )
        return RuntimeChatReply(
            answer=(
                f"MAS is {health['status']}. "
                f"There are {status['stored_tasks']} stored tasks, "
                f"{status['completed_tasks']} completed, and {status['running_tasks']} running."
                f"{latest_line}"
            ),
            source_task_id=latest.task_id if latest is not None else None,
            highlights=[
                f"stored_tasks={status['stored_tasks']}",
                f"completed_tasks={status['completed_tasks']}",
                f"running_tasks={status['running_tasks']}",
            ],
            cards=[
                RuntimeChatCard(
                    title="system:",
                    body=(
                        f"status {health['status']} | stored {status['stored_tasks']} | "
                        f"completed {status['completed_tasks']} | running {status['running_tasks']}"
                    ),
                ),
                RuntimeChatCard(
                    title="latest:",
                    body=latest_line.strip(),
                    action="showLastTask" if latest is not None else "analyzeWorkspace",
                    action_label="open" if latest is not None else "analyze",
                ),
            ],
            follow_up_actions=[
                RuntimeChatAction(action="healthCheck", label="health"),
                RuntimeChatAction(action="showLastTask", label="latest task"),
                RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
            ],
        )

    def _answer_recent_tasks(self, recent_tasks: list[RuntimeTask]) -> RuntimeChatReply:
        if not recent_tasks:
            return RuntimeChatReply(
                answer=(
                    "There are no MAS tasks yet. Run an analysis first so I have"
                    " results to reason over."
                ),
                recommended_action="analyzeWorkspace",
            )
        lines = [self._summarize_task(task) for task in recent_tasks]
        return RuntimeChatReply(
            answer="Here are the latest MAS tasks:\n- " + "\n- ".join(lines),
            source_task_id=recent_tasks[0].task_id,
            cards=[
                RuntimeChatCard(
                    title="recent:",
                    body=self._summarize_task(task),
                    action="showLastTask" if index == 0 else None,
                    action_label="open" if index == 0 else None,
                )
                for index, task in enumerate(recent_tasks)
            ],
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open latest"),
                RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
            ],
        )

    def _answer_summary(self, task: RuntimeTask | None) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer=(
                    "I do not have an analysis task to summarize yet. Run MAS"
                    " analysis and I can walk through the result."
                ),
                recommended_action="analyzeWorkspace",
            )

        result = task.result or {}
        repo_summary = result.get("repo_summary", {})
        explanation = str(result.get("explanation", "")).strip()
        files_scanned = repo_summary.get("files_scanned", "unknown")
        directories_scanned = repo_summary.get("directories_scanned", "unknown")
        answer = (
            f"Task {task.task_id} is {task.status}. "
            f"It analyzed {task.repo_path}, scanned {files_scanned} files across "
            f"{directories_scanned} directories, found {len(task.violations)} "
            f"violations, generated {len(task.hypotheses)} hypotheses, "
            f"and proposed {len(task.repairs)} repairs."
        )
        if explanation:
            answer += f" Explanation: {explanation}"
        return RuntimeChatReply(
            answer=answer,
            source_task_id=task.task_id,
            highlights=[
                f"violations={len(task.violations)}",
                f"hypotheses={len(task.hypotheses)}",
                f"repairs={len(task.repairs)}",
            ],
            cards=[
                RuntimeChatCard(
                    title="summary:",
                    body=(
                        f"{len(task.violations)} violations | "
                        f"{len(task.hypotheses)} hypotheses | "
                        f"{len(task.repairs)} repairs"
                    ),
                ),
                RuntimeChatCard(
                    title="repo:",
                    body=task.repo_path,
                    action="showLastTask",
                    action_label="open report",
                ),
            ],
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="report"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_violations(self, task: RuntimeTask | None) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no analysis task yet, so I cannot reason over violations.",
                recommended_action="analyzeWorkspace",
            )
        if not task.violations:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} has no recorded violations.",
                source_task_id=task.task_id,
            )
        counts: dict[str, int] = {}
        for violation in task.violations:
            counts[violation.severity] = counts.get(violation.severity, 0) + 1
        top_lines = [
            (
                f"[{item.severity}] {item.rule} @ {item.file_path or '(no file path)'}"
                f" -- {self._truncate(item.message or 'No message.', 120)}"
            )
            for item in task.violations[:3]
        ]
        severity_summary = ", ".join(f"{level}={count}" for level, count in sorted(counts.items()))
        return RuntimeChatReply(
            answer=(
                f"Task {task.task_id} has {len(task.violations)} violations ({severity_summary})."
                f" Top items:\n- " + "\n- ".join(top_lines)
            ),
            source_task_id=task.task_id,
            cards=[
                RuntimeChatCard(
                    title="violations:",
                    body=line,
                    action="showLastTask",
                    action_label="open task",
                )
                for line in top_lines
            ],
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open task"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_repairs(self, task: RuntimeTask | None) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no task with repair candidates yet.",
                recommended_action="analyzeWorkspace",
            )
        if not task.repairs:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} does not have repair candidates yet.",
                source_task_id=task.task_id,
            )
        lines = [
            (
                f"{item.repair_id} ({item.status}) -- "
                f"{self._truncate(item.description or 'No description.', 120)}"
            )
            for item in task.repairs[:3]
        ]
        proposed = sum(1 for item in task.repairs if item.status == "proposed")
        approved = sum(1 for item in task.repairs if item.status == "approved")
        return RuntimeChatReply(
            answer=(
                f"Task {task.task_id} has {len(task.repairs)} repair candidates "
                f"({proposed} proposed, {approved} approved). Top candidates:\n- "
                + "\n- ".join(lines)
            ),
            source_task_id=task.task_id,
            cards=[
                RuntimeChatCard(
                    title="repair:",
                    body=line,
                    action="showLastTask",
                    action_label="open task",
                )
                for line in lines
            ],
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open task"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_hypotheses(self, task: RuntimeTask | None) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no task with generated hypotheses yet.",
                recommended_action="analyzeWorkspace",
            )
        if not task.hypotheses:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} has no hypotheses yet.",
                source_task_id=task.task_id,
            )
        lines = [
            f"{item.title} -- {self._truncate(item.summary or 'No summary.', 140)}"
            for item in task.hypotheses[:3]
        ]
        return RuntimeChatReply(
            answer=f"Top hypotheses from {task.task_id}:\n- " + "\n- ".join(lines),
            source_task_id=task.task_id,
            cards=[
                RuntimeChatCard(
                    title="hypothesis:",
                    body=line,
                    action="showLastTask",
                    action_label="open task",
                )
                for line in lines
            ],
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open task"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_default(
        self,
        task: RuntimeTask | None,
        recent_tasks: list[RuntimeTask],
    ) -> RuntimeChatReply:
        if task is not None:
            return RuntimeChatReply(
                answer=(
                    "I can reason over the MAS state now. Ask for status, recent tasks, a summary,"
                    f" violations, repairs, or hypotheses from task {task.task_id}."
                ),
                source_task_id=task.task_id,
                follow_up_actions=[
                    RuntimeChatAction(action="showLastTask", label="open task"),
                    RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
                ],
            )
        if recent_tasks:
            return RuntimeChatReply(
                answer=(
                    "I can reason over recent MAS tasks. Ask me for status, summaries, violations,"
                    " repairs, or hypotheses."
                ),
                source_task_id=recent_tasks[0].task_id,
                follow_up_actions=[
                    RuntimeChatAction(action="showLastTask", label="latest task"),
                    RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
                ],
            )
        return RuntimeChatReply(
            answer=(
                "MAS is online, but there are no analysis tasks yet. Ask me to"
                " analyze this workspace to get started."
            ),
            intent="action",
            recommended_action="analyzeWorkspace",
            follow_up_actions=[
                RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
            ],
        )

    def _recent_tasks(self, tenant_id: str, limit: int) -> list[RuntimeTask]:
        return [
            task for task in self._runtime.recent_tasks(limit=max(limit * 3, limit))
            if task.tenant_id == tenant_id
        ][:limit]

    def _select_task(
        self,
        tenant_id: str,
        task_id: str | None,
        repo_path: str | None,
    ) -> RuntimeTask | None:
        if task_id:
            task = self._runtime.get_task(task_id)
            if task is not None and task.tenant_id == tenant_id:
                return task

        recent_tasks = self._recent_tasks(tenant_id=tenant_id, limit=20)
        if repo_path:
            for task in recent_tasks:
                if task.repo_path == repo_path:
                    return task
        return recent_tasks[0] if recent_tasks else None

    def _summarize_task(self, task: RuntimeTask) -> str:
        explanation = str(task.result.get("explanation", "")).strip()
        summary = (
            f"{task.task_id} is {task.status} for {task.repo_path} | "
            f"{len(task.violations)} violations | {len(task.hypotheses)} "
            f"hypotheses | {len(task.repairs)} repairs"
        )
        if explanation:
            summary += f" | summary: {self._truncate(explanation, 140)}"
        return summary

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[: max_length - 1].rstrip()}..."
