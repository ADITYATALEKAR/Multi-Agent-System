"""Stream consumer for message-based ingestion.

Subscribes to NATS JetStream subjects and forwards ingestion messages
to the IngestionPipeline. Designed to work with the ``nats-py`` client
when available; gracefully degrades if NATS is not installed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.core.fact import GraphDelta
from src.observability.logging import get_logger

logger = get_logger(__name__)


class StreamConsumer:
    """Consumes ingestion events from NATS JetStream.

    Message format::

        {
            "file_path": "/path/to/file.py",
            "source": "...optional source content...",
            "action": "analyze"  // or "delete"
        }

    Args:
        pipeline: The IngestionPipeline used for file analysis.
    """

    def __init__(self, pipeline: object) -> None:
        self._pipeline = pipeline
        self._nats_conn: Any | None = None
        self._subscription: Any | None = None
        self._running = False

    async def connect(self, nats_url: str = "nats://localhost:4222") -> None:
        """Connect to a NATS server.

        Attempts to import and use the ``nats`` package. Logs a warning
        and sets the connection to ``None`` if the package is unavailable.

        Args:
            nats_url: NATS server URL. Default ``nats://localhost:4222``.
        """
        try:
            import nats  # type: ignore[import-untyped]

            self._nats_conn = await nats.connect(nats_url)
            logger.info("nats_connected", url=nats_url)
        except ImportError:
            logger.warning(
                "nats_package_not_installed",
                detail="Install nats-py to enable stream consumption",
            )
            self._nats_conn = None
        except Exception as exc:
            logger.error("nats_connect_failed", url=nats_url, error=str(exc))
            self._nats_conn = None

    async def subscribe(self, subject: str = "ingestion.>") -> None:
        """Subscribe to an ingestion subject.

        Args:
            subject: NATS subject pattern. Default ``ingestion.>``.
        """
        if self._nats_conn is None:
            logger.warning("nats_not_connected", detail="Cannot subscribe without connection")
            return

        try:
            self._subscription = await self._nats_conn.subscribe(
                subject, cb=self._on_message
            )
            logger.info("nats_subscribed", subject=subject)
        except Exception as exc:
            logger.error(
                "nats_subscribe_failed",
                subject=subject,
                error=str(exc),
            )

    async def start(self) -> None:
        """Begin consuming messages.

        This simply marks the consumer as running. Messages are delivered
        asynchronously via the NATS subscription callback.
        """
        if self._nats_conn is None:
            logger.warning("nats_not_connected", detail="Cannot start without connection")
            return

        self._running = True
        logger.info("stream_consumer_started")

    async def stop(self) -> None:
        """Unsubscribe and disconnect from NATS."""
        self._running = False

        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
                logger.info("nats_unsubscribed")
            except Exception as exc:
                logger.error("nats_unsubscribe_failed", error=str(exc))
            self._subscription = None

        if self._nats_conn is not None:
            try:
                await self._nats_conn.close()
                logger.info("nats_disconnected")
            except Exception as exc:
                logger.error("nats_disconnect_failed", error=str(exc))
            self._nats_conn = None

    async def _on_message(self, msg: Any) -> None:
        """Handle an incoming NATS message.

        Parses the JSON payload and routes to the pipeline.
        """
        if not self._running:
            return

        try:
            data = json.loads(msg.data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("stream_message_decode_failed", error=str(exc))
            return

        file_path = data.get("file_path", "")
        if not file_path:
            logger.warning("stream_message_missing_file_path", data=data)
            return

        action = data.get("action", "analyze")
        source = data.get("source")

        if action == "delete":
            logger.info("stream_delete_action", file_path=file_path)
            # Deletion handling is a no-op in Phase 1; will be wired to
            # graph removal in a later phase.
            return

        if action == "analyze":
            try:
                deltas = await self._pipeline.ingest_file(file_path, source=source)
                logger.debug(
                    "stream_message_processed",
                    file_path=file_path,
                    delta_count=len(deltas),
                )
            except Exception as exc:
                logger.error(
                    "stream_message_processing_failed",
                    file_path=file_path,
                    error=str(exc),
                )
        else:
            logger.warning("stream_unknown_action", action=action, file_path=file_path)
