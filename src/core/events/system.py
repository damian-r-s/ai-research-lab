"""Event types for process lifecycle (evidence of restarts in the audit trail)."""

from __future__ import annotations

from src.core.events.registry import EventPayload, register


class ProcessLifecycle(EventPayload):
    component: str
    version: str
    hostname: str
    pid: int


PROCESS_STARTED = register(
    "system.process.started", 1, ProcessLifecycle, "A lab process started and is serving"
)
PROCESS_STOPPED = register(
    "system.process.stopped", 1, ProcessLifecycle, "A lab process shut down gracefully"
)
