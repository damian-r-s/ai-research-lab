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


class TaskFinished(EventPayload):
    run_id: str
    task_id: str
    node_path: str
    attempt_no: int
    outcome: str
    error: str | None = None


class ApprovalRequested(EventPayload):
    run_id: str
    approval_id: str
    kind: str


class ApprovalDecided(EventPayload):
    run_id: str
    approval_id: str
    approved: bool
    comment: str | None = None


class BudgetExceededEvent(EventPayload):
    run_id: str
    budget_id: str
    used_attempts: int
    used_seconds: float
    limits: dict[str, Any]


class BudgetExtended(EventPayload):
    run_id: str
    budget_id: str
    limits: dict[str, Any]


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
TASK_FINISHED = register("runs.task.finished", 1, TaskFinished, "An attempt of an agent task ended")
APPROVAL_REQUESTED = register(
    "runs.approval.requested", 1, ApprovalRequested, "A run asked for a human decision"
)
APPROVAL_DECIDED = register(
    "runs.approval.decided", 1, ApprovalDecided, "A person answered an approval request"
)
BUDGET_EXCEEDED = register(
    "runs.budget.exceeded", 1, BudgetExceededEvent, "A run used up a budget and was blocked"
)
BUDGET_EXTENDED = register(
    "runs.budget.extended", 1, BudgetExtended, "A budget was raised after an approval"
)
