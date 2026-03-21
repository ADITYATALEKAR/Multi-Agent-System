"""MemoryAgent — episodic memory store / query via coordination layer.

Uses src.memory.belief_store.BeliefStore when available; falls back to stub.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class MemoryAgent(BaseAgent):
    """Specialist agent for memory storage, querying, episode management, and belief updates."""

    AGENT_ID: str = "memory"
    CAPABILITIES: set[str] = {
        "memory_store",
        "memory_query",
        "episode_manage",
        "belief_update",
    }

    def execute(self, item: WorkItem) -> Any:
        """Store or query episodic memory based on item task_type / payload.

        Tries the real BeliefStore; returns stub on ImportError.
        """
        logger.info(
            "memory.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        action = (
            payload.get("action", "query") if isinstance(payload, dict) else "query"
        )

        try:
            from src.memory.belief_store import BeliefStore

            store = BeliefStore()
            if action == "store":
                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                store.store(data)
                logger.info("memory.stored")
                return {"action": "stored"}
            else:
                query = payload.get("query", "") if isinstance(payload, dict) else ""
                store.query(query)
                logger.info("memory.queried")
                return {"action": "queried"}
        except (ImportError, Exception) as exc:
            logger.warning("memory.fallback", reason=str(exc))
            return {"action": f"stub_{action}"}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 1.0
