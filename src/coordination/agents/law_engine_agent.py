"""LawEngineAgent evaluates repository hygiene and structural laws."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.coordination.agents.base import BaseAgent

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class LawEngineAgent(BaseAgent):
    """Specialist agent for law-check, violation detection, and compliance scanning."""

    AGENT_ID: str = "law_engine"
    CAPABILITIES: set[str] = {"law_check", "violation_detect", "compliance_scan"}
    _CACHE_DIR_RULES: dict[str, tuple[str, str, str]] = {
        ".import_linter_cache": (
            "repo.hygiene.import-linter-cache",
            "low",
            "Import Linter cache directory is present in the repository root.",
        ),
        ".pytest_cache": (
            "repo.hygiene.pytest-cache",
            "low",
            "Pytest cache directory is present in the repository root.",
        ),
        ".ruff_cache": (
            "repo.hygiene.ruff-cache",
            "low",
            "Ruff cache directory is present in the repository root.",
        ),
        ".mypy_cache": (
            "repo.hygiene.mypy-cache",
            "low",
            "Mypy cache directory is present in the repository root.",
        ),
        ".masi_runtime": (
            "repo.hygiene.runtime-state",
            "medium",
            "Local MAS runtime state is present in the repository workspace.",
        ),
        "temp": (
            "repo.hygiene.temp-dir",
            "medium",
            (
                "A temp directory exists in the repository root and should "
                "usually stay out of source control."
            ),
        ),
    }
    _VENV_DIRS = (".venv", ".venv312", "venv", "env")
    _SCRATCH_PATTERNS = ("_*.txt", "*.pid")

    def execute(self, item: WorkItem) -> Any:
        """Evaluate repository hygiene and structural laws from item payload."""
        logger.info(
            "law_engine.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        result = self._evaluate_repository(payload)
        logger.info("law_engine.evaluated", violations_found=result.get("violations_found", 0))
        return result

    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 1.5

    def _evaluate_repository(self, payload: dict[str, Any]) -> dict[str, Any]:
        graph_context = payload.get("graph_context") if isinstance(payload, dict) else None
        repo_summary = graph_context if isinstance(graph_context, dict) else {}
        repo_path = Path(str(repo_summary.get("path", "."))).expanduser()

        violations: list[dict[str, str]] = []
        if not repo_summary.get("exists", True):
            violations.append(
                self._violation(
                    rule="repository.exists",
                    severity="high",
                    file_path=repo_path,
                    message="The target repository path does not exist.",
                )
            )
        elif repo_path.is_file():
            violations.append(
                self._violation(
                    rule="repository.directory",
                    severity="medium",
                    file_path=repo_path,
                    message=(
                        "MAS analysis expects a repository directory, "
                        "but the target path is a file."
                    ),
                )
            )
        else:
            if not (repo_path / "README.md").exists():
                violations.append(
                    self._violation(
                        rule="repo.documentation.readme",
                        severity="medium",
                        file_path=repo_path / "README.md",
                        message="Repository is missing a README.md file.",
                    )
                )

            tests_dir = repo_path / "tests"
            if not tests_dir.exists():
                violations.append(
                    self._violation(
                        rule="repo.quality.tests",
                        severity="medium",
                        file_path=tests_dir,
                        message="Repository does not contain a tests directory.",
                    )
                )

            if (repo_path / "node_modules").exists():
                violations.append(
                    self._violation(
                        rule="repo.dependencies.vendored",
                        severity="high",
                        file_path=repo_path / "node_modules",
                        message="Vendored node_modules directory detected in repository root.",
                    )
                )

            venv_dirs = [
                repo_path / name for name in self._VENV_DIRS if (repo_path / name).exists()
            ]
            if venv_dirs:
                violations.append(
                    self._violation(
                        rule="repo.hygiene.virtualenv",
                        severity="high",
                        file_path=repo_path,
                        message=(
                            "Local virtual-environment directories are present "
                            "in the repository root: "
                            + ", ".join(path.name for path in venv_dirs)
                            + "."
                        ),
                    )
                )

            for dir_name, (rule, severity, message) in self._CACHE_DIR_RULES.items():
                candidate = repo_path / dir_name
                if candidate.exists():
                    violations.append(
                        self._violation(
                            rule=rule,
                            severity=severity,
                            file_path=candidate,
                            message=message,
                        )
                    )

            local_settings = repo_path / ".claude" / "settings.local.json"
            if local_settings.exists():
                violations.append(
                    self._violation(
                        rule="repo.hygiene.local-settings",
                        severity="medium",
                        file_path=local_settings,
                        message=(
                            "Local Claude settings file is present in the repository "
                            "and can leak workstation-specific configuration."
                        ),
                    )
                )

            scratch_files = self._collect_matches(
                repo_path,
                self._SCRATCH_PATTERNS,
                recursive=False,
            )
            if scratch_files:
                preview = ", ".join(path.name for path in scratch_files[:5])
                if len(scratch_files) > 5:
                    preview = f"{preview}, +{len(scratch_files) - 5} more"
                violations.append(
                    self._violation(
                        rule="repo.hygiene.scratch-files",
                        severity="medium",
                        file_path=repo_path,
                        message=(
                            "Scratch or process-tracking files are present in "
                            f"the repository root: {preview}."
                        ),
                    )
                )

            extension_dir = repo_path / "vscode-extension"
            if extension_dir.exists():
                vsix_files = self._collect_matches(
                    extension_dir,
                    ("*.vsix",),
                    recursive=True,
                    limit=12,
                )
                if vsix_files:
                    preview = ", ".join(path.name for path in vsix_files[:4])
                    if len(vsix_files) > 4:
                        preview = f"{preview}, +{len(vsix_files) - 4} more"
                    violations.append(
                        self._violation(
                            rule="repo.hygiene.extension-artifacts",
                            severity="medium",
                            file_path=extension_dir,
                            message=(
                                "Packaged VSIX artifacts are present in the "
                                f"workspace: {preview}."
                            ),
                        )
                    )

        return {
            "violations_found": len(violations),
            "violations": violations,
        }

    def _collect_matches(
        self,
        root: Path,
        patterns: Iterable[str],
        *,
        recursive: bool,
        limit: int | None = None,
    ) -> list[Path]:
        matches: list[Path] = []
        iterator = root.rglob if recursive else root.glob
        for pattern in patterns:
            for path in iterator(pattern):
                matches.append(path)
                if limit is not None and len(matches) >= limit:
                    return matches
        return matches

    def _violation(
        self,
        *,
        rule: str,
        severity: str,
        file_path: Path,
        message: str,
    ) -> dict[str, str]:
        normalized_path = str(file_path)
        entity_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_path))
        return {
            "rule": rule,
            "severity": severity,
            "file_path": normalized_path,
            "message": message,
            "entity_id": entity_id,
        }
