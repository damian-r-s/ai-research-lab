"""Audit event types emitted by the run lifecycle."""

from __future__ import annotations

from typing import Any

from src.core.events.registry import EventPayload, register


class RunCreated(EventPayload):
    run_id: str
    thread_id: str
    graph_key: str
    graph_version: str
    params: dict[str, Any]


class RunControlRequested(EventPayload):
    run_id: str
    reason: str | None = None


class RunStatusChanged(EventPayload):
    run_id: str
    from_status: str
    to_status: str
    reason: str | None = None


class KillSwitchChanged(EventPayload):
    engaged: bool
    reason: str | None
    runs_stopped: int
    runs_cancel_requested: int


RUN_CREATED = register("runs.run.created", 1, RunCreated, "A research run was created and queued")
PAUSE_REQUESTED = register(
    "runs.run.pause_requested", 1, RunControlRequested, "A pause of a run was requested"
)
RESUME_REQUESTED = register(
    "runs.run.resume_requested", 1, RunControlRequested, "A paused run was resumed"
)
STOP_REQUESTED = register(
    "runs.run.stop_requested", 1, RunControlRequested, "A stop of a run was requested"
)
STATUS_CHANGED = register(
    "runs.run.status_changed", 1, RunStatusChanged, "A run moved to another status"
)
KILL_SWITCH_CHANGED = register(
    "system.kill_switch.changed", 1, KillSwitchChanged, "The global kill switch was set or released"
)
