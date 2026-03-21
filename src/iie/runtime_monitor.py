"""IIE Runtime Monitor: watches for graph changes and triggers verification."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.engine import IIEEngine
from src.iie.passes.base import IntegrityViolation

log = structlog.get_logger(__name__)


class IIERuntimeMonitor:
    """Monitors system integrity at runtime.

    When started, it processes graph deltas by running the runtime
    passes (9-12) of the IIE engine.
    """

    def __init__(self, engine: IIEEngine) -> None:
        self._engine = engine
        self._running = False
        log.info("iie_runtime_monitor.init")

    def start(self) -> None:
        """Start the runtime monitor."""
        self._running = True
        log.info("iie_runtime_monitor.started")

    def stop(self) -> None:
        """Stop the runtime monitor."""
        self._running = False
        log.info("iie_runtime_monitor.stopped")

    def on_delta(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        """Called when a graph delta occurs -- runs runtime passes.

        Returns an empty list if the monitor is not running.
        """
        if not self._running:
            return []
        log.debug("iie_runtime_monitor.on_delta")
        return self._engine.run_runtime_passes(ir)

    @property
    def is_running(self) -> bool:
        """Whether the monitor is currently active."""
        return self._running
