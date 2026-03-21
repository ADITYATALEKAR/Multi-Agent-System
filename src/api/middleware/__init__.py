"""API middleware -- rate limiting and request guards."""

from __future__ import annotations

import time

import structlog

log = structlog.get_logger(__name__)


class RateLimiter:
    """In-memory sliding-window rate limiter keyed by tenant ID.

    Args:
        max_requests: Maximum number of requests allowed in a window.
        window_seconds: Length of the sliding window in seconds.
    """

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._limits: dict[str, list[float]] = {}  # tenant_id -> timestamps

    def _prune(self, tenant_id: str) -> None:
        """Remove timestamps outside the current window."""
        cutoff = time.time() - self._window_seconds
        self._limits[tenant_id] = [
            ts for ts in self._limits.get(tenant_id, []) if ts > cutoff
        ]

    def check_rate(self, tenant_id: str) -> bool:
        """Return ``True`` if the request is within the rate limit.

        Records the current timestamp when the check passes.
        """
        self._prune(tenant_id)
        timestamps = self._limits.setdefault(tenant_id, [])

        if len(timestamps) >= self._max_requests:
            log.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id,
                window=self._window_seconds,
            )
            return False

        timestamps.append(time.time())
        return True

    def get_retry_after(self, tenant_id: str) -> int:
        """Seconds until the oldest request in the window expires.

        Returns ``0`` when the tenant is not currently rate-limited.
        """
        self._prune(tenant_id)
        timestamps = self._limits.get(tenant_id, [])

        if len(timestamps) < self._max_requests:
            return 0

        oldest = min(timestamps)
        retry_after = int(oldest + self._window_seconds - time.time()) + 1
        return max(retry_after, 1)
