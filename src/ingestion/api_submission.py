"""API submission endpoint for direct source-code ingestion.

Provides a programmatic interface for submitting source code directly
into the IngestionPipeline without relying on files on disk.
"""

from __future__ import annotations

from typing import Any

from src.core.fact import GraphDelta
from src.observability.logging import get_logger

logger = get_logger(__name__)


class APISubmission:
    """Accepts direct API submissions and produces graph deltas.

    Args:
        pipeline: The IngestionPipeline used for analysis.
    """

    def __init__(self, pipeline: object) -> None:
        self._pipeline = pipeline

    async def submit(
        self,
        file_path: str,
        source: str,
        language: str | None = None,
    ) -> list[GraphDelta]:
        """Submit source code for analysis.

        Args:
            file_path: Logical file path (used for analyzer selection and metadata).
            source: Source code content.
            language: Optional language hint (currently informational).

        Returns:
            List of GraphDelta objects produced by the analyzer.

        Raises:
            ValueError: If *file_path* is empty or *source* is empty.
        """
        if not file_path or not file_path.strip():
            raise ValueError("file_path must be a non-empty string")
        if not source:
            raise ValueError("source must be a non-empty string")

        logger.info(
            "api_submission_received",
            file_path=file_path,
            language=language,
            source_length=len(source),
        )

        try:
            deltas = await self._pipeline.ingest_file(file_path, source=source)
            logger.info(
                "api_submission_complete",
                file_path=file_path,
                delta_count=len(deltas),
            )
            return deltas
        except Exception as exc:
            logger.error(
                "api_submission_failed",
                file_path=file_path,
                error=str(exc),
            )
            return []

    async def submit_batch(
        self, submissions: list[dict[str, Any]]
    ) -> list[GraphDelta]:
        """Submit multiple source files for analysis.

        Each entry in *submissions* must contain at least ``file_path``
        and ``source`` keys. An optional ``language`` key is accepted.

        Args:
            submissions: List of dicts with ``file_path``, ``source``,
                and optionally ``language``.

        Returns:
            Aggregated list of GraphDelta objects from all submissions.
        """
        all_deltas: list[GraphDelta] = []

        for i, entry in enumerate(submissions):
            file_path = entry.get("file_path", "")
            source = entry.get("source", "")
            language = entry.get("language")

            if not file_path or not source:
                logger.warning(
                    "api_batch_skip_invalid",
                    index=i,
                    has_path=bool(file_path),
                    has_source=bool(source),
                )
                continue

            try:
                deltas = await self.submit(file_path, source, language=language)
                all_deltas.extend(deltas)
            except ValueError as exc:
                logger.warning(
                    "api_batch_validation_error",
                    index=i,
                    file_path=file_path,
                    error=str(exc),
                )
            except Exception as exc:
                logger.error(
                    "api_batch_submit_error",
                    index=i,
                    file_path=file_path,
                    error=str(exc),
                )

        logger.info(
            "api_batch_complete",
            submitted=len(submissions),
            delta_count=len(all_deltas),
        )
        return all_deltas
