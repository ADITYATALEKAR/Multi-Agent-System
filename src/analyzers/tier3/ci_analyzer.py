"""CI/CD Pipeline Analyzer: YAML-based CI config extraction.

Supports GitHub Actions, GitLab CI, and generic YAML-based CI systems.
Detects CI configs by checking for keys like 'jobs', 'stages', 'pipelines',
'steps', 'workflows'. Extracts job names, step names, action references,
and images.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# Keys that strongly indicate a CI configuration file
_CI_INDICATOR_KEYS: set[str] = {
    "jobs", "stages", "pipelines", "steps", "workflows",
    "pipeline", "build", "deploy",
}

# GitHub Actions specific
_GHA_KEYS: set[str] = {"on", "jobs"}

# GitLab CI specific
_GITLAB_KEYS: set[str] = {"stages", "variables", "before_script", "after_script", "image"}


def _detect_ci_type(data: dict[str, Any], file_path: str) -> str | None:
    """Determine CI system type from the YAML structure and file path."""
    keys = set(data.keys())
    path_str = file_path.replace("\\", "/")

    # GitHub Actions: .github/workflows/*.yml
    if ".github/workflows/" in path_str or ".github/workflows\\" in file_path:
        if "on" in keys or "jobs" in keys:
            return "github_actions"

    # GitLab CI: .gitlab-ci.yml or has stages key
    if ".gitlab-ci" in Path(file_path).stem:
        return "gitlab_ci"
    if "stages" in keys and any(
        isinstance(data.get(k), dict) and "script" in (data[k] if isinstance(data[k], dict) else {})
        for k in keys - {"stages", "variables", "before_script", "after_script", "image", "default", "include", "workflow"}
    ):
        return "gitlab_ci"

    # Generic: has jobs/pipelines/steps
    if keys & _CI_INDICATOR_KEYS:
        return "generic_ci"

    return None


class CIAnalyzer(BaseAnalyzer):
    """YAML-based CI/CD pipeline configuration analyzer."""

    ANALYZER_ID = "ci"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Parse CI config YAML and emit graph deltas."""
        try:
            import yaml
        except ImportError:
            logger.warning("yaml_import_failed", reason="PyYAML not installed")
            return []

        try:
            data = yaml.safe_load(source)
        except Exception as exc:
            logger.debug("yaml_parse_failed", file_path=file_path, error=str(exc))
            return []

        if not isinstance(data, dict):
            return []

        ci_type = _detect_ci_type(data, file_path)
        if ci_type is None:
            return []

        if ci_type == "github_actions":
            return self._analyze_github_actions(data, file_path)
        elif ci_type == "gitlab_ci":
            return self._analyze_gitlab_ci(data, file_path)
        else:
            return self._analyze_generic_ci(data, file_path)

    # ── GitHub Actions ───────────────────────────────────────────────────

    def _analyze_github_actions(
        self, data: dict[str, Any], file_path: str
    ) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="yaml",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # Pipeline node (the workflow itself)
        workflow_name = data.get("name", Path(file_path).stem)
        trigger = data.get("on", {})
        trigger_str = self._stringify_trigger(trigger)

        pipeline_id = uuid4()
        ops.append(
            self._add_node(
                "ci_pipeline",
                workflow_name,
                file_path=file_path,
                language="yaml",
                node_id=pipeline_id,
                ci_type="github_actions",
                trigger=trigger_str,
            )
        )
        scope.add(pipeline_id)
        ops.append(self._add_edge(file_id, pipeline_id, "defines"))

        # Jobs
        jobs: dict[str, Any] = data.get("jobs", {}) or {}
        for job_name, job_spec in jobs.items():
            if not isinstance(job_spec, dict):
                continue

            runs_on = job_spec.get("runs-on", "")
            needs = job_spec.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            container = job_spec.get("container", {})
            job_image = ""
            if isinstance(container, str):
                job_image = container
            elif isinstance(container, dict):
                job_image = container.get("image", "")

            job_id = uuid4()
            ops.append(
                self._add_node(
                    "ci_job",
                    job_name,
                    file_path=file_path,
                    language="yaml",
                    node_id=job_id,
                    ci_type="github_actions",
                    runs_on=str(runs_on),
                    needs=needs,
                    image=job_image,
                )
            )
            scope.add(job_id)
            ops.append(self._add_edge(pipeline_id, job_id, "contains"))

            # Steps
            steps: list[dict[str, Any]] = job_spec.get("steps", []) or []
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_name = step.get("name", f"step_{idx}")
                uses = step.get("uses", "")
                run_cmd = step.get("run", "")
                step_with = step.get("with", {})

                step_id = uuid4()
                ops.append(
                    self._add_node(
                        "ci_step",
                        step_name,
                        file_path=file_path,
                        language="yaml",
                        node_id=step_id,
                        ci_type="github_actions",
                        uses=uses,
                        run=run_cmd[:200] if run_cmd else "",
                        step_index=idx,
                        step_with=step_with if isinstance(step_with, dict) else {},
                    )
                )
                scope.add(step_id)
                ops.append(self._add_edge(job_id, step_id, "contains"))

        if len(ops) <= 1:
            return []

        logger.debug(
            "ci_github_actions_analysis_complete",
            file_path=file_path,
            workflow=workflow_name,
            job_count=len(jobs),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── GitLab CI ────────────────────────────────────────────────────────

    def _analyze_gitlab_ci(
        self, data: dict[str, Any], file_path: str
    ) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="yaml",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # Pipeline node
        pipeline_id = uuid4()
        stages = data.get("stages", []) or []
        global_image = data.get("image", "")
        if isinstance(global_image, dict):
            global_image = global_image.get("name", "")

        ops.append(
            self._add_node(
                "ci_pipeline",
                Path(file_path).stem,
                file_path=file_path,
                language="yaml",
                node_id=pipeline_id,
                ci_type="gitlab_ci",
                stages=stages if isinstance(stages, list) else [],
                image=str(global_image),
            )
        )
        scope.add(pipeline_id)
        ops.append(self._add_edge(file_id, pipeline_id, "defines"))

        # Meta keys to skip (not job definitions)
        _meta_keys = {
            "stages", "variables", "before_script", "after_script",
            "image", "services", "cache", "default", "include",
            "workflow", "pages",
        }

        # Jobs
        for key, value in data.items():
            if key in _meta_keys or key.startswith("."):
                continue
            if not isinstance(value, dict):
                continue

            job_name = key
            stage = value.get("stage", "")
            job_image = value.get("image", global_image)
            if isinstance(job_image, dict):
                job_image = job_image.get("name", "")
            script = value.get("script", [])
            only = value.get("only", [])
            rules = value.get("rules", [])
            needs = value.get("needs", [])
            if isinstance(needs, list):
                needs = [
                    n if isinstance(n, str) else n.get("job", "")
                    for n in needs
                ]

            job_id = uuid4()
            ops.append(
                self._add_node(
                    "ci_job",
                    job_name,
                    file_path=file_path,
                    language="yaml",
                    node_id=job_id,
                    ci_type="gitlab_ci",
                    stage=str(stage),
                    image=str(job_image),
                    needs=needs if isinstance(needs, list) else [],
                )
            )
            scope.add(job_id)
            ops.append(self._add_edge(pipeline_id, job_id, "contains"))

            # Emit script lines as steps
            if isinstance(script, list):
                for idx, cmd in enumerate(script):
                    if not isinstance(cmd, str):
                        continue
                    step_id = uuid4()
                    ops.append(
                        self._add_node(
                            "ci_step",
                            f"{job_name}:script_{idx}",
                            file_path=file_path,
                            language="yaml",
                            node_id=step_id,
                            ci_type="gitlab_ci",
                            run=cmd[:200],
                            step_index=idx,
                        )
                    )
                    scope.add(step_id)
                    ops.append(self._add_edge(job_id, step_id, "contains"))

        if len(ops) <= 1:
            return []

        logger.debug(
            "ci_gitlab_analysis_complete",
            file_path=file_path,
            stage_count=len(stages),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Generic CI ───────────────────────────────────────────────────────

    def _analyze_generic_ci(
        self, data: dict[str, Any], file_path: str
    ) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="yaml",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # Pipeline node
        pipeline_id = uuid4()
        ops.append(
            self._add_node(
                "ci_pipeline",
                Path(file_path).stem,
                file_path=file_path,
                language="yaml",
                node_id=pipeline_id,
                ci_type="generic",
            )
        )
        scope.add(pipeline_id)
        ops.append(self._add_edge(file_id, pipeline_id, "defines"))

        # Try to extract jobs from common keys
        jobs_data: dict[str, Any] = {}
        for key in ("jobs", "pipelines", "build"):
            candidate = data.get(key)
            if isinstance(candidate, dict):
                jobs_data = candidate
                break

        if not jobs_data:
            # Try to extract steps from top-level 'steps' key
            steps_data = data.get("steps", [])
            if isinstance(steps_data, list):
                for idx, step in enumerate(steps_data):
                    if not isinstance(step, dict):
                        continue
                    step_name = step.get("name", step.get("label", f"step_{idx}"))
                    step_id = uuid4()
                    ops.append(
                        self._add_node(
                            "ci_step",
                            str(step_name),
                            file_path=file_path,
                            language="yaml",
                            node_id=step_id,
                            ci_type="generic",
                            step_index=idx,
                        )
                    )
                    scope.add(step_id)
                    ops.append(self._add_edge(pipeline_id, step_id, "contains"))

        for job_name, job_spec in jobs_data.items():
            if not isinstance(job_spec, dict):
                continue

            job_id = uuid4()
            image = ""
            if "image" in job_spec:
                img = job_spec["image"]
                image = img if isinstance(img, str) else str(img)
            elif "container" in job_spec:
                ctr = job_spec["container"]
                image = ctr if isinstance(ctr, str) else (ctr.get("image", "") if isinstance(ctr, dict) else "")

            ops.append(
                self._add_node(
                    "ci_job",
                    job_name,
                    file_path=file_path,
                    language="yaml",
                    node_id=job_id,
                    ci_type="generic",
                    image=image,
                )
            )
            scope.add(job_id)
            ops.append(self._add_edge(pipeline_id, job_id, "contains"))

            # Extract steps
            steps = job_spec.get("steps", job_spec.get("script", []))
            if isinstance(steps, list):
                for idx, step in enumerate(steps):
                    if isinstance(step, str):
                        step_name = step[:80]
                        step_meta: dict[str, Any] = {"run": step[:200]}
                    elif isinstance(step, dict):
                        step_name = step.get("name", step.get("label", f"step_{idx}"))
                        step_meta = {
                            "run": str(step.get("run", ""))[:200],
                            "uses": step.get("uses", ""),
                        }
                    else:
                        continue

                    step_id = uuid4()
                    ops.append(
                        self._add_node(
                            "ci_step",
                            str(step_name),
                            file_path=file_path,
                            language="yaml",
                            node_id=step_id,
                            ci_type="generic",
                            step_index=idx,
                            **step_meta,
                        )
                    )
                    scope.add(step_id)
                    ops.append(self._add_edge(job_id, step_id, "contains"))

        if len(ops) <= 2:
            # Only file + pipeline, nothing meaningful
            return []

        logger.debug(
            "ci_generic_analysis_complete",
            file_path=file_path,
            job_count=len(jobs_data),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _stringify_trigger(trigger: Any) -> str:
        """Convert GitHub Actions 'on' trigger to a readable string."""
        if isinstance(trigger, str):
            return trigger
        if isinstance(trigger, list):
            return ", ".join(str(t) for t in trigger)
        if isinstance(trigger, dict):
            return ", ".join(trigger.keys())
        return str(trigger)
