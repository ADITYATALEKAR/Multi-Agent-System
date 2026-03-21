"""RepoMapperAgent — maps repository structure via coordination layer.

Uses src.graph.repo_mapper.RepoMapper when available; falls back to stub.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class RepoMapperAgent(BaseAgent):
    """Specialist agent for repository structure mapping and dependency scanning."""

    AGENT_ID: str = "repo_mapper"
    CAPABILITIES: set[str] = {"repo_map", "dependency_scan", "structure_analysis"}

    _IGNORED_DIRS = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".turbo",
    }

    def execute(self, item: WorkItem) -> Any:
        """Map repository structure from WorkItem scope.

        Tries the real RepoMapper component; returns stub on ImportError.
        """
        logger.info(
            "repo_mapper.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
            scope_size=len(item.scope),
        )
        self.heartbeat()

        try:
            from src.graph.repo_mapper import RepoMapper

            mapper = RepoMapper()
            node_ids = list(item.scope) if item.scope else []
            result = mapper.map(node_ids)
            nodes_mapped = len(result) if result else 0
            logger.info("repo_mapper.mapped", nodes_mapped=nodes_mapped)
            return {"nodes_mapped": nodes_mapped}
        except (ImportError, Exception) as exc:
            logger.warning("repo_mapper.fallback", reason=str(exc))
            return self._fallback_map(item)

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 2.0

    def _fallback_map(self, item: WorkItem) -> dict[str, Any]:
        payload = item.payload if isinstance(item.payload, dict) else {}
        raw_path = payload.get("path") or "."
        repo_path = Path(str(raw_path)).expanduser()

        if not repo_path.exists():
            return {
                "nodes_mapped": 0,
                "repo_summary": {
                    "path": str(repo_path),
                    "exists": False,
                    "files_scanned": 0,
                    "directories_scanned": 0,
                    "top_extensions": [],
                },
            }

        if repo_path.is_file():
            files = [repo_path]
            directories_scanned = 1
        else:
            files = []
            directories_scanned = 0
            for path in repo_path.rglob("*"):
                if any(part in self._IGNORED_DIRS for part in path.parts):
                    continue
                if path.is_dir():
                    directories_scanned += 1
                    continue
                files.append(path)

        ext_counter = Counter(path.suffix.lower() or "<no_ext>" for path in files)
        top_extensions = [
            {"extension": ext, "count": count}
            for ext, count in ext_counter.most_common(10)
        ]
        summary = {
            "path": str(repo_path.resolve()),
            "exists": True,
            "files_scanned": len(files),
            "directories_scanned": directories_scanned,
            "top_extensions": top_extensions,
            "sample_files": [str(path.relative_to(repo_path)) for path in files[:20]]
            if repo_path.is_dir()
            else [repo_path.name],
        }
        return {"nodes_mapped": len(files), "repo_summary": summary}
