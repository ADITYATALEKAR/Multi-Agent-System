"""Webhook receiver for external event ingestion.

Accepts webhook payloads from GitHub, GitLab, and generic sources,
extracts changed file information, and feeds them to the IngestionPipeline.
Designed for integration with aiohttp/FastAPI but does not import them.
"""

from __future__ import annotations

import os
from typing import Any

from src.core.fact import GraphDelta
from src.observability.logging import get_logger

logger = get_logger(__name__)


class WebhookReceiver:
    """Handles incoming webhook payloads and produces graph deltas.

    Args:
        pipeline: The IngestionPipeline used for file analysis.
        repo_base_path: Base filesystem path where repositories are checked out.
            Used to resolve relative file paths from webhook payloads to
            absolute paths on disk.
    """

    def __init__(self, pipeline: object, repo_base_path: str = "") -> None:
        self._pipeline = pipeline
        self._repo_base_path = repo_base_path

    async def handle_github_webhook(
        self, payload: dict[str, Any]
    ) -> list[GraphDelta]:
        """Handle a GitHub push event webhook.

        Extracts added, modified, and removed files from the push event
        commits and ingests the added/modified ones.

        Expected payload structure (GitHub push event)::

            {
                "ref": "refs/heads/main",
                "repository": {"full_name": "owner/repo"},
                "commits": [
                    {
                        "added": ["file1.py"],
                        "modified": ["file2.py"],
                        "removed": ["old.py"]
                    }
                ]
            }

        Args:
            payload: Parsed JSON payload from a GitHub webhook.

        Returns:
            List of GraphDelta objects for the changed files.
        """
        try:
            commits = payload.get("commits", [])
            if not commits:
                logger.info("github_webhook_no_commits", payload_keys=list(payload.keys()))
                return []

            repo_name = payload.get("repository", {}).get("full_name", "unknown")
            ref = payload.get("ref", "unknown")

            changed_files: set[str] = set()
            for commit in commits:
                for f in commit.get("added", []):
                    changed_files.add(f)
                for f in commit.get("modified", []):
                    changed_files.add(f)
                # removed files are not ingested (nothing to analyze)

            if not changed_files:
                logger.info(
                    "github_webhook_no_changed_files",
                    repo=repo_name,
                    ref=ref,
                )
                return []

            logger.info(
                "github_webhook_processing",
                repo=repo_name,
                ref=ref,
                file_count=len(changed_files),
            )

            abs_paths = self._resolve_paths(changed_files)
            deltas = await self._pipeline.ingest_batch(abs_paths)

            logger.info(
                "github_webhook_complete",
                repo=repo_name,
                delta_count=len(deltas),
            )
            return deltas

        except Exception as exc:
            logger.error("github_webhook_failed", error=str(exc))
            return []

    async def handle_gitlab_webhook(
        self, payload: dict[str, Any]
    ) -> list[GraphDelta]:
        """Handle a GitLab push event webhook.

        Expected payload structure (GitLab push event)::

            {
                "ref": "refs/heads/main",
                "project": {"path_with_namespace": "group/project"},
                "commits": [
                    {
                        "added": ["file1.py"],
                        "modified": ["file2.py"],
                        "removed": ["old.py"]
                    }
                ]
            }

        Args:
            payload: Parsed JSON payload from a GitLab webhook.

        Returns:
            List of GraphDelta objects for the changed files.
        """
        try:
            commits = payload.get("commits", [])
            if not commits:
                logger.info("gitlab_webhook_no_commits", payload_keys=list(payload.keys()))
                return []

            project_name = payload.get("project", {}).get(
                "path_with_namespace", "unknown"
            )
            ref = payload.get("ref", "unknown")

            changed_files: set[str] = set()
            for commit in commits:
                for f in commit.get("added", []):
                    changed_files.add(f)
                for f in commit.get("modified", []):
                    changed_files.add(f)

            if not changed_files:
                logger.info(
                    "gitlab_webhook_no_changed_files",
                    project=project_name,
                    ref=ref,
                )
                return []

            logger.info(
                "gitlab_webhook_processing",
                project=project_name,
                ref=ref,
                file_count=len(changed_files),
            )

            abs_paths = self._resolve_paths(changed_files)
            deltas = await self._pipeline.ingest_batch(abs_paths)

            logger.info(
                "gitlab_webhook_complete",
                project=project_name,
                delta_count=len(deltas),
            )
            return deltas

        except Exception as exc:
            logger.error("gitlab_webhook_failed", error=str(exc))
            return []

    async def handle_generic_webhook(
        self, payload: dict[str, Any]
    ) -> list[GraphDelta]:
        """Handle a generic webhook with a list of files.

        Expected payload structure::

            {
                "files": [
                    {"path": "src/main.py", "source": "...optional..."},
                    {"path": "src/util.py"}
                ]
            }

        If ``source`` is provided per file it is passed directly to the
        pipeline; otherwise the file is read from disk.

        Args:
            payload: Parsed JSON payload with a ``files`` list.

        Returns:
            List of GraphDelta objects for the listed files.
        """
        try:
            files = payload.get("files", [])
            if not files:
                logger.info("generic_webhook_no_files")
                return []

            logger.info(
                "generic_webhook_processing",
                file_count=len(files),
            )

            all_deltas: list[GraphDelta] = []
            for entry in files:
                file_path = entry.get("path", "")
                if not file_path:
                    logger.warning("generic_webhook_missing_path", entry=entry)
                    continue

                source = entry.get("source")
                abs_path = self._resolve_single_path(file_path)
                deltas = await self._pipeline.ingest_file(abs_path, source=source)
                all_deltas.extend(deltas)

            logger.info(
                "generic_webhook_complete",
                delta_count=len(all_deltas),
            )
            return all_deltas

        except Exception as exc:
            logger.error("generic_webhook_failed", error=str(exc))
            return []

    # -- helpers --

    def _resolve_paths(self, relative_paths: set[str]) -> list[str]:
        """Resolve relative paths against the repo base path."""
        if self._repo_base_path:
            return [
                os.path.join(self._repo_base_path, fp) for fp in relative_paths
            ]
        return list(relative_paths)

    def _resolve_single_path(self, relative_path: str) -> str:
        """Resolve a single relative path against the repo base path."""
        if self._repo_base_path:
            return os.path.join(self._repo_base_path, relative_path)
        return relative_path
