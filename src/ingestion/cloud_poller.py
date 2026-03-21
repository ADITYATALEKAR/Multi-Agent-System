"""Cloud storage poller for ingestion.

Periodically polls an S3-compatible bucket for new or changed objects
and feeds them into the IngestionPipeline. Uses a generic async HTTP
pattern so it does not depend on boto3 or any specific cloud SDK.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from src.core.fact import GraphDelta
from src.observability.logging import get_logger

logger = get_logger(__name__)


class CloudPoller:
    """Polls cloud storage (S3-like) for new or changed files.

    Tracks object keys and their ETags / last-modified timestamps
    between polls. When new or changed objects are detected they
    are fed to the IngestionPipeline.

    Args:
        pipeline: The IngestionPipeline used for file analysis.
        fetch_object: Async callable ``(bucket, key) -> str`` that retrieves
            the textual content of a cloud object. Injected for testability.
        list_objects: Async callable ``(bucket, prefix) -> list[dict]`` that
            returns a list of dicts with at least ``key`` and ``etag`` fields.
            Injected for testability.
    """

    def __init__(
        self,
        pipeline: object,
        list_objects: Any | None = None,
        fetch_object: Any | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._list_objects = list_objects
        self._fetch_object = fetch_object
        self._running = False
        self._task: asyncio.Task | None = None
        # key -> etag/hash of last seen version
        self._seen: dict[str, str] = {}

    async def start(
        self,
        bucket: str,
        prefix: str = "",
        interval: int = 60,
    ) -> None:
        """Begin polling the cloud bucket.

        Args:
            bucket: Bucket name.
            prefix: Optional key prefix filter.
            interval: Seconds between polls. Default 60.
        """
        if self._running:
            logger.warning("cloud_poller_already_running")
            return

        if self._list_objects is None or self._fetch_object is None:
            logger.error(
                "cloud_poller_missing_callbacks",
                detail="list_objects and fetch_object callables must be provided",
            )
            return

        self._running = True
        logger.info(
            "cloud_poller_started",
            bucket=bucket,
            prefix=prefix,
            interval=interval,
        )
        self._task = asyncio.create_task(
            self._poll_loop(bucket, prefix, interval)
        )

    async def stop(self) -> None:
        """Stop the polling loop."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("cloud_poller_stopped")

    async def _poll_loop(
        self, bucket: str, prefix: str, interval: int
    ) -> None:
        """Internal loop: list objects, detect changes, ingest."""
        try:
            while self._running:
                try:
                    await self._poll_once(bucket, prefix)
                except Exception as exc:
                    logger.error(
                        "cloud_poller_poll_error",
                        bucket=bucket,
                        prefix=prefix,
                        error=str(exc),
                    )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _poll_once(self, bucket: str, prefix: str) -> list[GraphDelta]:
        """Execute a single poll cycle.

        Returns:
            Deltas produced in this cycle (also useful for testing).
        """
        objects = await self._list_objects(bucket, prefix)
        if not objects:
            return []

        new_or_changed: list[dict[str, str]] = []
        for obj in objects:
            key = obj.get("key", "")
            etag = obj.get("etag", "")
            if not key:
                continue
            if self._seen.get(key) != etag:
                new_or_changed.append(obj)

        if not new_or_changed:
            return []

        logger.info(
            "cloud_poller_changes_detected",
            bucket=bucket,
            prefix=prefix,
            count=len(new_or_changed),
        )

        all_deltas: list[GraphDelta] = []
        for obj in new_or_changed:
            key = obj["key"]
            try:
                source = await self._fetch_object(bucket, key)
                deltas = await self._pipeline.ingest_file(key, source=source)
                all_deltas.extend(deltas)
                # Update seen state after successful ingestion
                self._seen[key] = obj.get("etag", "")
            except Exception as exc:
                logger.error(
                    "cloud_poller_ingest_failed",
                    bucket=bucket,
                    key=key,
                    error=str(exc),
                )

        logger.info(
            "cloud_poller_cycle_complete",
            bucket=bucket,
            delta_count=len(all_deltas),
        )
        return all_deltas
