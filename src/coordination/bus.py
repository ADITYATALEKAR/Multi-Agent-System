"""Synchronous message bus for inter-agent communication.

Implements:
- TypedMessage envelope with subject, sender, payload
- Publish-subscribe with topic filtering
- Synchronous transport (no asyncio)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class TypedMessage(BaseModel):
    """Envelope for inter-agent messages."""

    message_id: UUID = Field(default_factory=uuid4)
    subject: str
    sender: str  # agent_id
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class MessageBus:
    """Synchronous publish-subscribe message bus for agent-to-agent messaging.

    - Topic-based routing
    - Multiple subscribers per topic
    - Synchronous delivery (callbacks invoked inline)
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[TypedMessage], None]]] = {}
        self._message_count: int = 0

    def publish(self, subject: str, sender: str, payload: dict[str, Any] | None = None) -> TypedMessage:
        """Publish a message to a subject.

        Args:
            subject: The topic/subject to publish to.
            sender: ID of the sending agent.
            payload: Message data.

        Returns:
            The published TypedMessage.
        """
        msg = TypedMessage(subject=subject, sender=sender, payload=payload or {})
        self._message_count += 1

        callbacks = self._subscribers.get(subject, [])
        for cb in callbacks:
            try:
                cb(msg)
            except Exception:
                logger.warning("subscriber_error", subject=subject, sender=sender)

        logger.debug(
            "message_published",
            subject=subject,
            sender=sender,
            subscribers=len(callbacks),
        )
        return msg

    def subscribe(self, subject: str, callback: Callable[[TypedMessage], None]) -> None:
        """Subscribe to a subject with a callback.

        Args:
            subject: The topic/subject to subscribe to.
            callback: Callable invoked on each message.
        """
        if subject not in self._subscribers:
            self._subscribers[subject] = []
        self._subscribers[subject].append(callback)

    def unsubscribe(self, subject: str, callback: Callable[[TypedMessage], None]) -> None:
        """Remove a subscriber from a subject."""
        if subject in self._subscribers:
            self._subscribers[subject] = [
                cb for cb in self._subscribers[subject] if cb is not callback
            ]

    def subscriber_count(self, subject: str) -> int:
        """Return the number of subscribers for a subject."""
        return len(self._subscribers.get(subject, []))

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def topics(self) -> list[str]:
        return list(self._subscribers.keys())
