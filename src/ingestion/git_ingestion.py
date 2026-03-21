"""Git repository ingestion.

Walks a local Git repository (excluding .git/) and extracts structural
facts via the IngestionPipeline. Supports full-repo and diff-based ingestion.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from src.core.fact import GraphDelta
from src.observability.logging import get_logger

logger = get_logger(__name__)


class GitIngestion:
    """Extracts facts from Git repositories.

    Args:
        pipeline: The IngestionPipeline used for file analysis.
    """

    def __init__(self, pipeline: object) -> None:
        # Avoid circular import by accepting the pipeline as a generic object.
        # At runtime this will be an IngestionPipeline instance.
        self._pipeline = pipeline

    async def ingest_repo(
        self, repo_path: str, ref: str = "HEAD"
    ) -> list[GraphDelta]:
        """Ingest an entire Git repository (excluding .git/).

        Walks the repo directory and feeds every file to the pipeline.

        Args:
            repo_path: Filesystem path to the Git repository root.
            ref: Git ref (informational, used for logging). Defaults to ``"HEAD"``.

        Returns:
            List of GraphDelta objects representing repository facts.
        """
        repo = Path(repo_path)
        if not repo.is_dir():
            logger.error("repo_path_not_found", repo_path=repo_path)
            return []

        file_paths: list[str] = []
        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            dirs[:] = [d for d in dirs if d != ".git"]
            for fname in files:
                file_paths.append(os.path.join(root, fname))

        logger.info(
            "git_repo_ingestion_started",
            repo_path=repo_path,
            ref=ref,
            file_count=len(file_paths),
        )

        deltas = await self._pipeline.ingest_batch(file_paths)

        logger.info(
            "git_repo_ingestion_complete",
            repo_path=repo_path,
            ref=ref,
            delta_count=len(deltas),
        )
        return deltas

    async def ingest_diff(
        self, repo_path: str, from_ref: str, to_ref: str
    ) -> list[GraphDelta]:
        """Ingest only files changed between two Git refs.

        Runs ``git diff --name-only`` to find changed files and ingests each one.

        Args:
            repo_path: Filesystem path to the Git repository root.
            from_ref: Starting Git ref (e.g. a commit SHA or branch name).
            to_ref: Ending Git ref.

        Returns:
            List of GraphDelta objects for the changed files.
        """
        changed = await self.get_changed_files(repo_path, from_ref, to_ref)
        if not changed:
            logger.info(
                "git_diff_no_changes",
                repo_path=repo_path,
                from_ref=from_ref,
                to_ref=to_ref,
            )
            return []

        # Resolve to absolute paths relative to repo root
        abs_paths = [os.path.join(repo_path, fp) for fp in changed]
        # Filter to files that actually exist on disk (deletions won't)
        existing = [p for p in abs_paths if os.path.isfile(p)]

        logger.info(
            "git_diff_ingestion_started",
            repo_path=repo_path,
            from_ref=from_ref,
            to_ref=to_ref,
            changed_count=len(changed),
            existing_count=len(existing),
        )

        deltas = await self._pipeline.ingest_batch(existing)

        logger.info(
            "git_diff_ingestion_complete",
            repo_path=repo_path,
            from_ref=from_ref,
            to_ref=to_ref,
            delta_count=len(deltas),
        )
        return deltas

    async def get_changed_files(
        self, repo_path: str, from_ref: str, to_ref: str
    ) -> list[str]:
        """Run ``git diff --name-only`` and return relative file paths.

        Uses ``asyncio.create_subprocess_exec`` for non-blocking execution.

        Args:
            repo_path: Filesystem path to the Git repository root.
            from_ref: Starting Git ref.
            to_ref: Ending Git ref.

        Returns:
            List of relative file paths changed between the two refs.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--name-only",
                from_ref,
                to_ref,
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    "git_diff_command_failed",
                    repo_path=repo_path,
                    from_ref=from_ref,
                    to_ref=to_ref,
                    returncode=proc.returncode,
                    stderr=stderr.decode().strip(),
                )
                return []

            output = stdout.decode().strip()
            if not output:
                return []

            files = [line.strip() for line in output.splitlines() if line.strip()]
            logger.debug(
                "git_diff_files_found",
                repo_path=repo_path,
                from_ref=from_ref,
                to_ref=to_ref,
                count=len(files),
            )
            return files

        except FileNotFoundError:
            logger.error("git_not_found", detail="git executable not on PATH")
            return []
        except Exception as exc:
            logger.error(
                "git_diff_unexpected_error",
                repo_path=repo_path,
                error=str(exc),
            )
            return []
