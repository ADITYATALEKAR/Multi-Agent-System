"""Filesystem watcher for continuous ingestion.

Monitors directories for file changes using asyncio-based polling (no
external watchdog dependency). Detects new and modified files by
tracking modification times.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable

from src.observability.logging import get_logger

logger = get_logger(__name__)


class FileWatcher:
    """Watches filesystem paths and triggers callbacks on changes.

    Uses periodic polling to detect file modifications and additions.
    No external dependencies beyond asyncio and os.

    Args:
        poll_interval: Seconds between filesystem scans. Default 2.
    """

    def __init__(self, poll_interval: float = 2.0) -> None:
        self._poll_interval = poll_interval
        self._callbacks: list[Callable] = []
        self._running = False
        self._task: asyncio.Task | None = None
        # path -> last known mtime
        self._mtimes: dict[str, float] = {}

    def on_change(self, callback: Callable) -> None:
        """Register a callback invoked when file changes are detected.

        The callback receives a list of changed file paths (``list[str]``).
        It may be a coroutine function (async) or a regular function.

        Args:
            callback: Function or coroutine accepting ``list[str]``.
        """
        self._callbacks.append(callback)
        logger.debug("file_watcher_callback_registered", callback=repr(callback))

    async def start(self, watch_dir: str) -> None:
        """Begin watching a directory for file changes.

        Runs an infinite polling loop in a background task. Call
        :meth:`stop` to terminate.

        Args:
            watch_dir: Directory path to monitor recursively.
        """
        if self._running:
            logger.warning("file_watcher_already_running", watch_dir=watch_dir)
            return

        self._running = True
        self._watch_dir = watch_dir

        # Build initial snapshot so we don't fire on every existing file
        self._mtimes = self._scan_directory(watch_dir)
        logger.info(
            "file_watcher_started",
            watch_dir=watch_dir,
            initial_files=len(self._mtimes),
            poll_interval=self._poll_interval,
        )

        self._task = asyncio.create_task(self._poll_loop(watch_dir))

    async def stop(self) -> None:
        """Stop the file watcher."""
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
        logger.info("file_watcher_stopped")

    async def _poll_loop(self, watch_dir: str) -> None:
        """Internal polling loop that checks for file changes."""
        try:
            while self._running:
                await asyncio.sleep(self._poll_interval)
                if not self._running:
                    break
                try:
                    current = self._scan_directory(watch_dir)
                    changed = self._detect_changes(current)
                    if changed:
                        logger.debug(
                            "file_changes_detected",
                            count=len(changed),
                            files=changed[:10],  # Log at most 10 paths
                        )
                        await self._notify(changed)
                    self._mtimes = current
                except Exception as exc:
                    logger.error(
                        "file_watcher_poll_error",
                        error=str(exc),
                    )
        except asyncio.CancelledError:
            pass

    def _scan_directory(self, watch_dir: str) -> dict[str, float]:
        """Scan directory recursively and collect file mtimes."""
        result: dict[str, float] = {}
        try:
            for root, dirs, files in os.walk(watch_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        result[fpath] = os.path.getmtime(fpath)
                    except OSError:
                        pass
        except OSError as exc:
            logger.warning("scan_directory_failed", watch_dir=watch_dir, error=str(exc))
        return result

    def _detect_changes(self, current: dict[str, float]) -> list[str]:
        """Compare current snapshot against previous, return changed paths."""
        changed: list[str] = []

        # New or modified files
        for path, mtime in current.items():
            prev_mtime = self._mtimes.get(path)
            if prev_mtime is None or mtime > prev_mtime:
                changed.append(path)

        return changed

    async def _notify(self, changed_paths: list[str]) -> None:
        """Invoke all registered callbacks with the changed paths."""
        for callback in self._callbacks:
            try:
                result = callback(changed_paths)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error(
                    "file_watcher_callback_error",
                    callback=repr(callback),
                    error=str(exc),
                )
