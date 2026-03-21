"""Ingestion pipeline orchestrator.

Coordinates data ingestion from multiple sources through the AnalyzerHarness,
produces GraphDelta objects, and optionally persists them to DeltaLogStore.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from src.analyzers.harness import AnalyzerHarness
from src.core.fact import GraphDelta
from src.observability.logging import get_logger
from src.observability.metrics import (
    blueprint_ingestion_files_total,
    blueprint_ingestion_errors_total,
    blueprint_ingestion_duration_seconds,
)

logger = get_logger(__name__)


class IngestionPipeline:
    """Main ingestion pipeline that accepts source files and produces graph deltas.

    Orchestrates the AnalyzerHarness, optionally stores deltas in DeltaLogStore,
    and supports configurable concurrency.

    Args:
        harness: The AnalyzerHarness instance for running analyzers.
        delta_store: Optional DeltaLogStore for persisting deltas.
        semantic_cache: Optional semantic cache for deduplication.
        max_concurrency: Maximum number of concurrent file ingestions.
    """

    def __init__(
        self,
        harness: AnalyzerHarness,
        delta_store: Any | None = None,
        semantic_cache: Any | None = None,
        max_concurrency: int = 10,
    ) -> None:
        self._harness = harness
        self._delta_store = delta_store
        self._semantic_cache = semantic_cache
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def ingest_file(
        self, file_path: str, source: str | None = None
    ) -> list[GraphDelta]:
        """Ingest a single file and return graph deltas.

        Reads the file if *source* is not provided, runs the appropriate
        analyzer via the harness, and optionally stores deltas.

        Args:
            file_path: Path to the source file.
            source: Optional pre-loaded source content.

        Returns:
            List of GraphDelta objects produced by the analyzer.
        """
        async with self._semaphore:
            try:
                if source is None:
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            source = f.read()
                    except (OSError, IOError) as exc:
                        logger.warning(
                            "file_read_failed",
                            file_path=file_path,
                            error=str(exc),
                        )
                        blueprint_ingestion_errors_total.inc()
                        return []

                with blueprint_ingestion_duration_seconds.time():
                    deltas = await self._harness.analyze_file(file_path, source)

                # Persist to delta store if available
                if self._delta_store is not None:
                    for delta in deltas:
                        try:
                            await self._delta_store.append(delta)
                        except Exception as exc:
                            logger.error(
                                "delta_store_append_failed",
                                file_path=file_path,
                                delta_id=str(delta.delta_id),
                                error=str(exc),
                            )

                blueprint_ingestion_files_total.inc()
                logger.debug(
                    "file_ingested",
                    file_path=file_path,
                    delta_count=len(deltas),
                )
                return deltas

            except Exception as exc:
                logger.error(
                    "ingest_file_failed",
                    file_path=file_path,
                    error=str(exc),
                )
                blueprint_ingestion_errors_total.inc()
                return []

    async def ingest_directory(self, dir_path: str) -> list[GraphDelta]:
        """Walk a directory recursively and ingest all supported files.

        Args:
            dir_path: Path to the directory to ingest.

        Returns:
            Aggregated list of GraphDelta objects from all files.
        """
        all_deltas: list[GraphDelta] = []
        file_paths: list[str] = []

        for root, _dirs, files in os.walk(dir_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                # Skip hidden directories (e.g. .git)
                rel = os.path.relpath(fpath, dir_path)
                parts = Path(rel).parts
                if any(part.startswith(".") for part in parts):
                    continue
                file_paths.append(fpath)

        if not file_paths:
            logger.info("no_files_found", dir_path=dir_path)
            return all_deltas

        logger.info(
            "directory_ingestion_started",
            dir_path=dir_path,
            file_count=len(file_paths),
        )

        all_deltas = await self.ingest_batch(file_paths)

        logger.info(
            "directory_ingestion_complete",
            dir_path=dir_path,
            delta_count=len(all_deltas),
        )
        return all_deltas

    async def ingest_batch(self, file_paths: list[str]) -> list[GraphDelta]:
        """Ingest multiple files in parallel with concurrency control.

        Args:
            file_paths: List of file paths to ingest.

        Returns:
            Aggregated list of GraphDelta objects from all files.
        """
        tasks = [self.ingest_file(fp) for fp in file_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_deltas: list[GraphDelta] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "batch_ingest_file_error",
                    file_path=file_paths[i],
                    error=str(result),
                )
                blueprint_ingestion_errors_total.inc()
            elif isinstance(result, list):
                all_deltas.extend(result)

        return all_deltas
