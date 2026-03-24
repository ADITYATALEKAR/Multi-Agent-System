"""Backend chat reasoning over MAS runtime state and analysis results."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.service import MASIRuntime, RuntimeTask, RuntimeWorkItem


@dataclass(slots=True)
class RuntimeChatReply:
    """Structured response returned by the backend chat service."""

    answer: str
    intent: str = "answer"
    recommended_action: str | None = None
    source_task_id: str | None = None
    summary: str | None = None
    actions_taken: list[str] = field(default_factory=list)
    files_in_focus: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)
    symbols_in_focus: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    next_step: str | None = None
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


@dataclass(slots=True)
class WorkspaceSnapshot:
    """Live workspace inspection snapshot for agent-style answers."""

    repo_path: str | None = None
    branch: str | None = None
    available: bool = False
    changed_paths: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GitStatusEntry:
    """Parsed git status record for workspace inspection."""

    status: str
    path: str


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
        workspace_snapshot = self._inspect_workspace(
            repo_path
            or (selected_task.repo_path if selected_task is not None else None)
            or (recent_tasks[0].repo_path if recent_tasks else None)
        )

        action_reply = self._detect_action_intent(normalized, selected_task)
        if action_reply is not None:
            return action_reply

        if "health" in normalized or "status" in normalized:
            return self._answer_status(recent_tasks, workspace_snapshot)

        if any(
            token in normalized
            for token in {"latest task", "last task", "recent task", "recent analysis"}
        ):
            return self._answer_recent_tasks(recent_tasks, workspace_snapshot)

        if any(
            token in normalized
            for token in {
                "edit plan",
                "patch plan",
                "fix plan",
                "how should we edit",
                "how should we fix",
                "plan the change",
            }
        ):
            plan_reply = self._answer_edit_plan(prompt, workspace_snapshot, selected_task)
            if plan_reply is not None:
                return plan_reply

        if any(
            token in normalized
            for token in {"apply approved edits", "apply the patch", "make the change"}
        ):
            return RuntimeChatReply(
                answer=(
                    "I can apply the approved edit now. I will use your connected LLM"
                    " to rewrite the target file, then report what changed back here."
                ),
                intent="action",
                recommended_action="applyApprovedEdits",
                summary="MAS is ready to apply the approved edit to the target file.",
                suggestions=[
                    "Make sure the file path and change request are explicit in your prompt.",
                    "After the edit, review the diff and run the smallest validation step.",
                ],
                next_step=(
                    "Apply the approved edit, then inspect the diff and validation"
                    " result."
                ),
            )

        if any(
            token in normalized
            for token in {"explain file", "inspect file", "open file", "look at file"}
        ) or self._contains_explicit_path(prompt):
            file_reply = self._answer_file_inspection(prompt, workspace_snapshot, selected_task)
            if file_reply is not None:
                return file_reply

        if any(token in normalized for token in {"summary", "summarize", "explain", "overview"}):
            return self._answer_summary(selected_task, workspace_snapshot)

        if any(
            token in normalized
            for token in {"what changed", "changes", "files changed", "file changes"}
        ):
            return self._answer_progress(selected_task, workspace_snapshot)

        if "violation" in normalized:
            return self._answer_violations(selected_task, workspace_snapshot)

        if "repair" in normalized:
            return self._answer_repairs(selected_task, workspace_snapshot)

        if "hypoth" in normalized:
            return self._answer_hypotheses(selected_task, workspace_snapshot)

        if any(token in normalized for token in {"next step", "what next", "suggest", "recommend"}):
            return self._answer_guidance(selected_task, recent_tasks, workspace_snapshot)

        if any(token in normalized for token in {"repo", "path", "workspace"}) and repo_path:
            return RuntimeChatReply(
                answer=f"I am analyzing this workspace path: {repo_path}",
                summary=f"workspace {repo_path}",
                files_changed=workspace_snapshot.files_changed,
                code_changes=workspace_snapshot.code_changes,
                suggestions=[
                    "Ask for status to check the runtime.",
                    "Ask me to analyze this workspace.",
                ],
                next_step="If you want fresh results, ask MAS to analyze this workspace.",
                highlights=[repo_path],
            )

        return self._answer_default(selected_task, recent_tasks, workspace_snapshot)

    def _detect_action_intent(
        self,
        normalized_prompt: str,
        selected_task: RuntimeTask | None,
    ) -> RuntimeChatReply | None:
        wants_project_read = (
            any(
                token in normalized_prompt
                for token in {
                    "read my entire project",
                    "read the entire project",
                    "read this project",
                    "review my project",
                    "review this project",
                    "understand this project",
                    "summarize this project",
                    "summarize the project",
                    "project summary",
                    "repo summary",
                    "repository summary",
                    "codebase summary",
                }
            )
            or (
                any(
                    token in normalized_prompt
                    for token in {
                        "read",
                        "review",
                        "understand",
                        "summarize",
                        "summary",
                        "overview",
                    }
                )
                and any(
                    token in normalized_prompt
                    for token in {"project", "repo", "repository", "workspace", "codebase"}
                )
            )
        )
        if wants_project_read and (
            selected_task is None or selected_task.status.lower() != "completed"
        ):
            latest = (
                f" The latest task I know about is {selected_task.task_id}"
                f" and it is still {selected_task.status}."
                if selected_task is not None
                else ""
            )
            return RuntimeChatReply(
                answer=(
                    "To read the whole project and give you a meaningful summary, I"
                    " should run a fresh workspace analysis first." + latest
                ),
                intent="action",
                recommended_action="analyzeWorkspace",
                source_task_id=selected_task.task_id if selected_task is not None else None,
                summary=(
                    "MAS needs a completed workspace analysis before it can produce"
                    " a reliable whole-project summary."
                ),
                suggestions=[
                    "Run analyze on the current workspace.",
                    "After that, ask for a project summary or the top findings.",
                ],
                next_step="Run analyze, then ask MAS to summarize the project.",
                follow_up_actions=[
                    RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
                ],
            )
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
                summary="MAS needs the local runtime before chat and analysis can work end to end.",
                suggestions=[
                    "Run setup first, then start the API.",
                    "After setup, run health to confirm the backend is reachable.",
                ],
                next_step="Run setup, then start api.",
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
                summary=(
                    "The runtime looks ready, but the local API needs to be running on"
                    " 127.0.0.1:8000."
                ),
                suggestions=[
                    "Start the API, then run health.",
                    "After health passes, ask MAS to analyze the current workspace.",
                ],
                next_step="Run start api, then health.",
                follow_up_actions=[
                    RuntimeChatAction(action="startApi", label="start api"),
                ],
            )
        if "health" in normalized_prompt and "check" in normalized_prompt:
            return RuntimeChatReply(
                answer="I should run a live MAS health check against the local API.",
                intent="action",
                recommended_action="healthCheck",
                summary=(
                    "A health check will confirm whether the local MAS API is reachable"
                    " right now."
                ),
                next_step="Run health to confirm the API is up.",
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
                summary=(
                    "MAS can run the repo analysis pipeline and refresh the latest task"
                    " with violations, hypotheses, repairs, and an explanation."
                ),
                files_in_focus=[selected_task.repo_path] if selected_task is not None else [],
                suggestions=[
                    "Run analyze to get a fresh snapshot.",
                    "After analysis, ask MAS for a summary or top violations.",
                ],
                next_step="Run analyze, then ask for the latest task summary.",
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
                summary=(
                    "Opening the latest task is the fastest way to inspect the newest"
                    " analysis details."
                ),
                next_step="Open the latest task, then ask MAS to summarize or explain it.",
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
                summary=(
                    "MAS can connect to an external LLM and use it as the"
                    " natural-language front door."
                ),
                suggestions=[
                    "Choose a provider, model, and API key.",
                    "After connecting, try a plain-English instruction like 'start the api'.",
                ],
                next_step="Open connect llm and save your provider settings.",
                follow_up_actions=[
                    RuntimeChatAction(action="configureProvider", label="connect llm"),
                ],
            )
        return None

    def _answer_status(
        self,
        recent_tasks: list[RuntimeTask],
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
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
            summary=(
                f"MAS is {health['status']} with {status['stored_tasks']} stored tasks,"
                f" {status['completed_tasks']} completed, and {status['running_tasks']} running."
            ),
            actions_taken=(
                [self._format_work_item_line(item) for item in latest.work_items[:4]]
                if latest is not None
                else []
            ),
            files_in_focus=self._merge_focus_files(latest, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for the latest task summary.",
                "Ask for top violations or repair candidates.",
            ],
            next_step=(
                "Open the latest task to inspect the current repo snapshot."
                if latest is not None
                else "Run analyze on the current workspace to create the first task."
            ),
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

    def _answer_recent_tasks(
        self,
        recent_tasks: list[RuntimeTask],
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if not recent_tasks:
            return RuntimeChatReply(
                answer=(
                    "There are no MAS tasks yet. Run an analysis first so I have"
                    " results to reason over."
                ),
                recommended_action="analyzeWorkspace",
                summary="There is no recent MAS task yet.",
                next_step="Run analyze on the current workspace.",
            )
        lines = [self._summarize_task(task) for task in recent_tasks]
        return RuntimeChatReply(
            answer="Here are the latest MAS tasks:\n- " + "\n- ".join(lines),
            source_task_id=recent_tasks[0].task_id,
            summary=(
                f"I found {len(recent_tasks)} recent MAS tasks and the newest one is"
                f" {recent_tasks[0].task_id}."
            ),
            actions_taken=[self._summarize_task(task) for task in recent_tasks],
            files_in_focus=self._merge_focus_files(recent_tasks[0], workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask me to summarize the latest task.",
                "Ask for violations, repairs, or hypotheses.",
            ],
            next_step="Open the latest task or ask for its summary.",
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

    def _answer_summary(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer=(
                    "I do not have an analysis task to summarize yet. Run MAS"
                    " analysis and I can walk through the result."
                ),
                recommended_action="analyzeWorkspace",
                summary="There is no analysis result to summarize yet.",
                next_step="Run analyze, then ask for a summary again.",
            )
        if task.status.lower() != "completed":
            return RuntimeChatReply(
                answer=(
                    f"The latest task {task.task_id} is still {task.status}, so I do"
                    " not have a finished project summary yet."
                ),
                intent="action",
                recommended_action="analyzeWorkspace",
                source_task_id=task.task_id,
                summary=(
                    f"{task.task_id} is still {task.status}; MAS needs a completed"
                    " analysis before the summary will be meaningful."
                ),
                files_in_focus=self._merge_focus_files(task, workspace_snapshot),
                suggestions=[
                    "Run analyze again to refresh the workspace task.",
                    "After it completes, ask for the project summary.",
                ],
                next_step="Run analyze, then ask for the summary again.",
                follow_up_actions=[
                    RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
                    RuntimeChatAction(action="showLastTask", label="last task"),
                ],
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
            summary=(
                f"{task.task_id} is {task.status} for {task.repo_path} with "
                f"{len(task.violations)} violations, {len(task.hypotheses)} hypotheses,"
                f" and {len(task.repairs)} repairs."
            ),
            actions_taken=[self._format_work_item_line(item) for item in task.work_items[:5]],
            files_in_focus=self._merge_focus_files(task, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for top violations if you want the main risks.",
                "Ask for repairs if you want the proposed fixes.",
                "Ask for hypotheses if you want likely root causes.",
            ],
            next_step=(
                "Open the latest task report or ask MAS for a more specific slice"
                " of the result."
            ),
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

    def _answer_violations(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no analysis task yet, so I cannot reason over violations.",
                recommended_action="analyzeWorkspace",
                summary="MAS needs an analysis task before it can list violations.",
                next_step="Run analyze, then ask for violations again.",
            )
        if not task.violations:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} has no recorded violations.",
                source_task_id=task.task_id,
                summary=f"{task.task_id} currently has no recorded violations.",
                next_step="If you expect issues, rerun analysis on the current workspace.",
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
            summary=(
                f"{task.task_id} has {len(task.violations)} violations across"
                f" {severity_summary}."
            ),
            actions_taken=[self._format_work_item_line(item) for item in task.work_items[:4]],
            files_in_focus=self._merge_focus_files(task, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for repairs to see what MAS proposes next.",
                "Open the task if you want the full violation list.",
            ],
            next_step="Review the top violation files, then ask for repairs or hypotheses.",
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

    def _answer_repairs(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no task with repair candidates yet.",
                recommended_action="analyzeWorkspace",
                summary="MAS needs an analysis task before it can propose repairs.",
                next_step="Run analyze, then ask for repair candidates again.",
            )
        if not task.repairs:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} does not have repair candidates yet.",
                source_task_id=task.task_id,
                summary=f"{task.task_id} has no repair candidates yet.",
                next_step="Ask for violations or rerun analysis if you expected repair proposals.",
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
            summary=(
                f"{task.task_id} has {len(task.repairs)} repair candidates with "
                f"{proposed} proposed and {approved} approved."
            ),
            actions_taken=[self._format_work_item_line(item) for item in task.work_items[:4]],
            files_in_focus=self._merge_focus_files(task, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Open the task report for the full repair list.",
                "Ask for hypotheses if you want likely causes behind the repairs.",
            ],
            next_step="Review the top repair candidates and then inspect the associated files.",
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

    def _answer_hypotheses(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer="There is no task with generated hypotheses yet.",
                recommended_action="analyzeWorkspace",
                summary="MAS needs an analysis task before it can generate hypotheses.",
                next_step="Run analyze, then ask for hypotheses again.",
            )
        if not task.hypotheses:
            return RuntimeChatReply(
                answer=f"Task {task.task_id} has no hypotheses yet.",
                source_task_id=task.task_id,
                summary=f"{task.task_id} does not have stored hypotheses yet.",
                next_step="Ask for a summary or rerun analysis if you expected hypotheses.",
            )
        lines = [
            f"{item.title} -- {self._truncate(item.summary or 'No summary.', 140)}"
            for item in task.hypotheses[:3]
        ]
        return RuntimeChatReply(
            answer=f"Top hypotheses from {task.task_id}:\n- " + "\n- ".join(lines),
            source_task_id=task.task_id,
            summary=(
                f"{task.task_id} has {len(task.hypotheses)} stored hypotheses."
                " Here are the top ones."
            ),
            actions_taken=[self._format_work_item_line(item) for item in task.work_items[:4]],
            files_in_focus=self._merge_focus_files(task, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for violations to compare symptoms with the hypotheses.",
                "Ask for repairs if you want proposed next actions.",
            ],
            next_step=(
                "Compare the top hypotheses with the violation list, then inspect"
                " the suggested repairs."
            ),
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

    def _answer_file_inspection(
        self,
        prompt: str,
        workspace_snapshot: WorkspaceSnapshot,
        task: RuntimeTask | None,
    ) -> RuntimeChatReply | None:
        target = self._resolve_file_target(prompt, workspace_snapshot, task)
        if target is None:
            return None

        symbol = self._extract_symbol_candidate(prompt)
        summary, excerpt_lines, symbols = self._summarize_file(target, symbol)
        relative_path = self._relative_to_repo(target, workspace_snapshot.repo_path)
        diff_snippet = self._build_single_change_snippet(
            Path(workspace_snapshot.repo_path),
            GitStatusEntry(status="M", path=relative_path),
        ) if workspace_snapshot.repo_path else ""

        return RuntimeChatReply(
            answer=(
                f"I inspected {relative_path}."
                + (
                    f" I also focused on the symbol {symbol}."
                    if symbol and symbols
                    else (
                        " I can see the file shape and the most relevant lines"
                        " to orient the next change."
                    )
                )
            ),
            source_task_id=task.task_id if task is not None else None,
            summary=summary,
            actions_taken=["inspected file contents", "captured lightweight structural summary"],
            files_in_focus=[relative_path],
            files_changed=workspace_snapshot.files_changed,
            code_changes=[snippet for snippet in [diff_snippet, *excerpt_lines] if snippet][:4],
            symbols_in_focus=symbols,
            suggestions=[
                "Ask MAS for an edit plan if you want a safe change sequence.",
                "Ask what changed if you want the broader workspace context.",
            ],
            next_step="Review the file summary, then ask for an edit plan before changing code.",
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="last task"),
            ],
        )

    def _answer_edit_plan(
        self,
        prompt: str,
        workspace_snapshot: WorkspaceSnapshot,
        task: RuntimeTask | None,
    ) -> RuntimeChatReply | None:
        targets = self._resolve_file_targets(prompt, workspace_snapshot, task, limit=3)
        if not targets:
            return None

        plan_steps = [
            "Confirm the user-facing behavior we want to preserve before editing.",
            "Make the smallest coherent changes first, file by file.",
            "Validate each file with the narrowest possible command before running broader checks.",
        ]
        cards: list[RuntimeChatCard] = []
        focus_files: list[str] = []
        if task is not None:
            matching_violations = [
                item.rule
                for item in task.violations
                if any(item.file_path.strip().endswith(target.name) for target in targets)
            ][:3]
        else:
            matching_violations = []

        for target in targets:
            relative_path = self._relative_to_repo(target, workspace_snapshot.repo_path)
            focus_files.append(relative_path)
            file_plan = [
                f"inspect {relative_path}",
                "make the smallest necessary edit",
                *self._validation_steps_for_file(target),
            ]
            cards.append(
                RuntimeChatCard(
                    title=f"file: {relative_path}",
                    body="\n".join(file_plan),
                )
            )

        suggestions = [
            "Ask MAS to explain this file if you want more local context.",
            "After editing, rerun the smallest validation step first.",
        ]
        if matching_violations:
            suggestions.insert(
                0,
                "Keep these task signals in mind: " + ", ".join(matching_violations),
            )

        return RuntimeChatReply(
            answer=(
                "I can sketch a safe edit plan grouped by file. The goal is to preserve"
                " current behavior, change the smallest unit possible, and validate"
                " immediately after each edit."
            ),
            source_task_id=task.task_id if task is not None else None,
            summary=(
                "Prepared a grouped patch plan for "
                + ", ".join(focus_files)
                + "."
            ),
            actions_taken=plan_steps,
            files_in_focus=focus_files,
            files_changed=workspace_snapshot.files_changed,
            code_changes=[
                self._build_file_header(target) for target in targets
            ],
            suggestions=suggestions,
            next_step=(
                "Start with the first file in the patch plan, then validate"
                " before moving on."
            ),
            cards=cards,
        )

    def _answer_progress(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is None:
            return RuntimeChatReply(
                answer=(
                    "I do not have a MAS task yet, so there are no workspace"
                    " changes to walk through."
                ),
                summary="No MAS task is available yet.",
                next_step="Run analyze, then ask what changed.",
                recommended_action="analyzeWorkspace",
            )
        actions_taken = [self._format_work_item_line(item) for item in task.work_items[:5]]
        return RuntimeChatReply(
            answer=(
                f"For {task.task_id}, MAS completed {len(task.work_items)} pipeline steps. "
                "I can show you the task flow, the live changed files, and the"
                " main files now in focus."
            ),
            source_task_id=task.task_id,
            summary=(
                f"{task.task_id} completed {len(task.work_items)} pipeline steps"
                f" for {task.repo_path}."
            ),
            actions_taken=actions_taken,
            files_in_focus=self._merge_focus_files(task, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for the latest summary if you want the full picture.",
                "Ask for violations if you want the concrete issues.",
            ],
            next_step="Inspect the files in focus, then ask for repairs or hypotheses.",
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open task"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_guidance(
        self,
        task: RuntimeTask | None,
        recent_tasks: list[RuntimeTask],
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        selected = task or (recent_tasks[0] if recent_tasks else None)
        if selected is None:
            return RuntimeChatReply(
                answer=(
                    "The best next step is to analyze the current workspace so MAS"
                    " has something concrete to reason over."
                ),
                intent="action",
                recommended_action="analyzeWorkspace",
                summary="MAS needs an analysis task before it can guide the next engineering step.",
                suggestions=[
                    "Run analyze on the current workspace.",
                    "After that, ask for a summary or top violations.",
                ],
                next_step="Run analyze on the current workspace.",
                follow_up_actions=[
                    RuntimeChatAction(action="analyzeWorkspace", label="analyze"),
                ],
            )
        return RuntimeChatReply(
            answer=(
                f"The next useful move after {selected.task_id} is to inspect the summary,"
                " then drill into violations, repairs, or hypotheses depending on whether"
                " you want risks, fixes, or likely causes."
            ),
            source_task_id=selected.task_id,
            summary=f"MAS is ready to guide the next step from {selected.task_id}.",
            actions_taken=[self._format_work_item_line(item) for item in selected.work_items[:4]],
            files_in_focus=self._merge_focus_files(selected, workspace_snapshot),
            files_changed=workspace_snapshot.files_changed,
            code_changes=workspace_snapshot.code_changes,
            suggestions=[
                "Ask for a summary for the big picture.",
                "Ask for violations for concrete risks.",
                "Ask for repairs for proposed fixes.",
                "Ask for hypotheses for likely causes.",
            ],
            next_step="Start with a summary, then choose violations, repairs, or hypotheses.",
            follow_up_actions=[
                RuntimeChatAction(action="showLastTask", label="open task"),
                RuntimeChatAction(action="analyzeWorkspace", label="reanalyze"),
            ],
        )

    def _answer_default(
        self,
        task: RuntimeTask | None,
        recent_tasks: list[RuntimeTask],
        workspace_snapshot: WorkspaceSnapshot,
    ) -> RuntimeChatReply:
        if task is not None:
            return RuntimeChatReply(
                answer=(
                    "I can reason over the MAS state now. Ask for status, recent tasks, a summary,"
                    f" violations, repairs, or hypotheses from task {task.task_id}."
                ),
                source_task_id=task.task_id,
                summary=f"MAS is ready and the active task is {task.task_id}.",
                actions_taken=[self._format_work_item_line(item) for item in task.work_items[:4]],
                files_in_focus=self._merge_focus_files(task, workspace_snapshot),
                files_changed=workspace_snapshot.files_changed,
                code_changes=workspace_snapshot.code_changes,
                suggestions=[
                    "Ask for a summary if you want the whole picture.",
                    "Ask for violations, repairs, or hypotheses if you want a specific slice.",
                ],
                next_step="Start with a summary, then drill into the area you care about.",
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
                summary=(
                    "MAS has recent task history and the newest task is"
                    f" {recent_tasks[0].task_id}."
                ),
                actions_taken=[self._summarize_task(task) for task in recent_tasks[:3]],
                files_in_focus=self._merge_focus_files(recent_tasks[0], workspace_snapshot),
                files_changed=workspace_snapshot.files_changed,
                code_changes=workspace_snapshot.code_changes,
                suggestions=[
                    "Ask for the latest task summary.",
                    "Ask for violations or repairs if you want concrete findings.",
                ],
                next_step="Ask MAS to summarize the latest task.",
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
            summary="MAS is online, but there is no workspace analysis yet.",
            suggestions=[
                "Run analyze on the current workspace.",
                "After that, ask MAS for a summary or the top violations.",
            ],
            next_step="Run analyze on the current workspace.",
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

    def _collect_file_paths(self, task: RuntimeTask | None, limit: int = 5) -> list[str]:
        if task is None:
            return []
        file_paths: list[str] = []
        seen: set[str] = set()
        for violation in task.violations:
            path = violation.file_path.strip()
            if path and path not in seen:
                seen.add(path)
                file_paths.append(path)
            if len(file_paths) >= limit:
                return file_paths
        if not file_paths:
            file_paths.append(task.repo_path)
        return file_paths[:limit]

    def _merge_focus_files(
        self,
        task: RuntimeTask | None,
        workspace_snapshot: WorkspaceSnapshot,
        limit: int = 6,
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        task_paths = self._collect_file_paths(task, limit=limit)
        for path in [*workspace_snapshot.files_changed, *task_paths]:
            normalized = path.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
            if len(merged) >= limit:
                break
        return merged

    def _resolve_file_target(
        self,
        prompt: str,
        workspace_snapshot: WorkspaceSnapshot,
        task: RuntimeTask | None,
    ) -> Path | None:
        targets = self._resolve_file_targets(prompt, workspace_snapshot, task, limit=1)
        return targets[0] if targets else None

    def _resolve_file_targets(
        self,
        prompt: str,
        workspace_snapshot: WorkspaceSnapshot,
        task: RuntimeTask | None,
        limit: int = 3,
    ) -> list[Path]:
        repo_root = Path(workspace_snapshot.repo_path) if workspace_snapshot.repo_path else None
        resolved_targets: list[Path] = []
        seen: set[str] = set()
        for candidate in self._extract_path_candidates(prompt):
            resolved = self._resolve_path_candidate(candidate, repo_root)
            if resolved is not None and str(resolved) not in seen:
                seen.add(str(resolved))
                resolved_targets.append(resolved)
            if len(resolved_targets) >= limit:
                return resolved_targets

        known_paths = workspace_snapshot.changed_paths[:]
        if task is not None:
            known_paths.extend(self._collect_file_paths(task, limit=8))
        if repo_root is not None:
            for candidate in known_paths:
                resolved = self._resolve_path_candidate(candidate, repo_root)
                if resolved is not None and str(resolved) not in seen:
                    seen.add(str(resolved))
                    resolved_targets.append(resolved)
                if len(resolved_targets) >= limit:
                    break
        return resolved_targets

    def _extract_path_candidates(self, prompt: str) -> list[str]:
        matches = re.findall(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)", prompt)
        ordered: list[str] = []
        for match in matches:
            normalized = match.strip("`'\"")
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        return ordered

    @staticmethod
    def _contains_explicit_path(prompt: str) -> bool:
        return bool(re.search(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+", prompt))

    @staticmethod
    def _extract_symbol_candidate(prompt: str) -> str | None:
        patterns = [
            r"(?:function|method|symbol)\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"(?:class)\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _resolve_path_candidate(self, candidate: str, repo_root: Path | None) -> Path | None:
        raw = candidate.replace("\\", "/").strip()
        direct = Path(raw)
        if direct.is_absolute() and direct.exists() and direct.is_file():
            return direct
        if repo_root is not None:
            repo_candidate = (repo_root / raw).resolve()
            if repo_candidate.exists() and repo_candidate.is_file():
                return repo_candidate
            basename_matches = list(repo_root.rglob(Path(raw).name)) if Path(raw).name else []
            for match in basename_matches[:3]:
                if match.is_file():
                    return match
        return None

    def _summarize_file(self, path: Path, symbol: str | None) -> tuple[str, list[str], list[str]]:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return (
                (
                    f"{path.name} is a non-text or unreadable file, so MAS can only"
                    " reference its path."
                ),
                [self._build_file_header(path)],
                [],
            )

        lines = raw.splitlines()
        non_empty = [line.rstrip() for line in lines if line.strip()]
        interesting = [
            line.strip()
            for line in non_empty
            if line.lstrip().startswith(
                ("def ", "class ", "interface ", "function ", "const ", "export ", "import ")
            )
        ][:6]
        if symbol:
            symbol_summary = self._extract_symbol_summary(lines, symbol)
            if symbol_summary is not None:
                return (
                    symbol_summary[0],
                    [f"{path.name}\n{symbol_summary[1]}"],
                    [symbol],
                )
        summary = (
            f"{path.name} has {len(lines)} lines."
            + (
                " Key structure: " + "; ".join(self._truncate(line, 80) for line in interesting[:3])
                if interesting
                else (
                    " MAS did not find obvious structural markers, so this may be"
                    " mostly data or prose."
                )
            )
        )
        preview_source = interesting if interesting else non_empty[:6]
        preview_lines = [
            f"{path.name}\n" + "\n".join(preview_source[:4])
        ] if preview_source else [f"{path.name}\nempty file"]
        return summary, preview_lines, interesting[:3]

    def _extract_symbol_summary(self, lines: list[str], symbol: str) -> tuple[str, str] | None:
        patterns = [
            re.compile(rf"^\s*def\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*class\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*(?:async\s+)?function\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*(?:const|let|var)\s+{re.escape(symbol)}\b"),
            re.compile(rf"^\s*{re.escape(symbol)}\s*[:=]\s*"),
        ]
        for index, line in enumerate(lines):
            if any(pattern.search(line) for pattern in patterns):
                block = [line.rstrip()]
                for follower in lines[index + 1:index + 5]:
                    if follower.strip():
                        block.append(follower.rstrip())
                summary = (
                    f"{symbol} appears in this file and MAS extracted the surrounding block"
                    " so we can reason about that symbol directly."
                )
                return summary, "\n".join(block[:5])
        return None

    @staticmethod
    def _build_file_header(path: Path) -> str:
        return f"{path.name}\npath: {path}"

    def _relative_to_repo(self, path: Path, repo_path: str | None) -> str:
        if repo_path:
            try:
                return str(path.relative_to(Path(repo_path)))
            except ValueError:
                pass
        return str(path)

    def _validation_steps_for_file(self, path: Path) -> list[str]:
        suffix = path.suffix.lower()
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            return [
                "Run the nearest TypeScript or frontend compile step.",
                "Smoke-test the related UI flow after the change.",
            ]
        if suffix == ".py":
            return [
                "Run the smallest pytest target that covers this module.",
                "Run ruff on the touched Python files.",
            ]
        if suffix in {".rs"}:
            return [
                "Run cargo check or the smallest relevant cargo test.",
            ]
        return [
            "Run the smallest validation step that covers this file.",
        ]

    def _inspect_workspace(self, repo_path: str | None) -> WorkspaceSnapshot:
        if not repo_path:
            return WorkspaceSnapshot()
        root = Path(repo_path).expanduser()
        if not root.exists():
            return WorkspaceSnapshot(repo_path=str(root))
        if not (root / ".git").exists():
            return WorkspaceSnapshot(repo_path=str(root))

        status_output = self._run_git(root, ["status", "--short"])
        branch_output = self._run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])

        status_entries = self._parse_git_status(status_output)
        files_changed = [
            f"{entry.status} {entry.path}" for entry in status_entries[:6]
        ]
        code_changes = self._build_code_change_snippets(root, status_entries[:4])

        return WorkspaceSnapshot(
            repo_path=str(root),
            branch=branch_output.strip() or None,
            available=True,
            changed_paths=[entry.path for entry in status_entries],
            files_changed=files_changed,
            code_changes=code_changes,
        )

    @staticmethod
    def _run_git(repo_root: Path, args: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return ""
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()

    def _parse_git_status(self, status_output: str) -> list[GitStatusEntry]:
        entries: list[GitStatusEntry] = []
        for line in status_output.splitlines():
            if not line.strip():
                continue
            status = line[:2].strip() or "?"
            raw_path = line[3:].strip() if len(line) > 3 else line.strip()
            path = raw_path.split(" -> ")[-1].strip()
            entries.append(GitStatusEntry(status=status, path=path))
        return entries

    def _build_code_change_snippets(
        self,
        repo_root: Path,
        entries: list[GitStatusEntry],
    ) -> list[str]:
        snippets: list[str] = []
        for entry in entries:
            snippet = self._build_single_change_snippet(repo_root, entry)
            if snippet:
                snippets.append(snippet)
        return snippets[:4]

    def _build_single_change_snippet(self, repo_root: Path, entry: GitStatusEntry) -> str:
        if entry.status == "??":
            return self._preview_untracked_file(repo_root, entry.path)

        diff_text = self._run_git(
            repo_root,
            ["diff", "--unified=0", "--no-ext-diff", "--", entry.path],
        )
        if not diff_text:
            diff_text = self._run_git(
                repo_root,
                ["diff", "--cached", "--unified=0", "--no-ext-diff", "--", entry.path],
            )
        if not diff_text:
            return f"{entry.path}\nstatus: {entry.status}"

        lines: list[str] = [entry.path]
        kept = 0
        for line in diff_text.splitlines():
            if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---")):
                lines.append(line)
                kept += 1
            if kept >= 8:
                break
        if len(lines) == 1:
            lines.append("diff available, but no compact hunk preview was extracted")
        return "\n".join(lines)

    def _preview_untracked_file(self, repo_root: Path, relative_path: str) -> str:
        candidate = repo_root / relative_path
        try:
            raw = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return f"{relative_path}\nnew file staged in workspace"

        preview_lines = [
            line.rstrip()
            for line in raw.splitlines()
            if line.strip()
        ][:6]
        if not preview_lines:
            return f"{relative_path}\nnew empty file"
        preview = "\n".join(preview_lines)
        return f"{relative_path}\n{self._truncate(preview, 320)}"

    @staticmethod
    def _format_work_item_line(item: RuntimeWorkItem) -> str:
        task_type = getattr(item, "task_type", "unknown")
        status = getattr(item, "status", "unknown")
        return f"{task_type} -> {status}"

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[: max_length - 1].rstrip()}..."
