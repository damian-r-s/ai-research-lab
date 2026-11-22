"""Run lifecycle use cases (ARCHITECTURE §6.6). Callers own the transaction.

Every state change is recorded in `research_runs`, appended to the audit log and, where a runner
must react, signalled on `NOTIFY lab_control`, all in the same transaction. The notification is
delivered only on commit, so a runner never acts on a change that was rolled back.

Concurrency:
- every control locks the run row (`SELECT ... FOR UPDATE`);
- starting or resuming takes a share lock on the kill-switch row, and engaging the switch takes
  an exclusive one, so a run cannot slip in while the switch is being engaged.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.runs import ResearchRun, SystemState
from src.core.errors import ConflictError, KillSwitchEngaged, NotFound
from src.core.events import store
from src.core.events.store import EventContext
from src.core.ids import uuid7
from src.runs import events
from src.runs.dispatch import Dispatcher
from src.runs.states import IDLE, TERMINAL, can_transition

CONTROL_CHANNEL = "lab_control"
ControlAction = Literal["pause", "stop", "kill"]


@dataclass(frozen=True, slots=True)
class KillSwitch:
    engaged: bool
    reason: str | None
    changed_by: str | None


def _actor_label(context: EventContext) -> str:
    return f"{context.actor.type}:{context.actor.id}" if context.actor.id else context.actor.type


async def _notify(session: AsyncSession, action: ControlAction, run_id: uuid.UUID | None) -> None:
    payload = json.dumps({"action": action, "run_id": str(run_id) if run_id else None})
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": CONTROL_CHANNEL, "payload": payload},
    )


async def lock_run(session: AsyncSession, run_id: uuid.UUID) -> ResearchRun:
    run = await session.scalar(
        select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
    )
    if run is None:
        raise NotFound(f"run {run_id} does not exist")
    return run


async def _kill_switch_row(session: AsyncSession, *, exclusive: bool) -> SystemState:
    query = select(SystemState).where(SystemState.id == 1)
    query = query.with_for_update() if exclusive else query.with_for_update(read=True)
    row = await session.scalar(query)
    if row is None:  # created by migration 0003; missing means a broken database
        raise RuntimeError("system_state row is missing")
    return row


async def _refuse_if_killed(session: AsyncSession) -> None:
    state = await _kill_switch_row(session, exclusive=False)
    if state.kill_switch:
        raise KillSwitchEngaged(
            "the kill switch is engaged; nothing can start or resume",
            details={"reason": state.reason},
        )


async def transition(
    session: AsyncSession,
    run: ResearchRun,
    to_status: str,
    *,
    context: EventContext,
    reason: str | None = None,
) -> ResearchRun:
    """Move a (row-locked) run to another status, or raise ConflictError if not allowed."""
    if not can_transition(run.status, to_status):
        raise ConflictError(
            f"run cannot go from {run.status} to {to_status}",
            details={"run_id": str(run.id), "status": run.status},
        )
    now = utcnow()
    from_status = run.status
    run.status = to_status
    run.version += 1
    if to_status == "running" and run.started_at is None:
        run.started_at = now
    if to_status in TERMINAL:
        run.ended_at = now
        run.pause_requested = False
    if to_status == "paused":
        run.pause_requested = False
    await store.append(
        session,
        type_=events.STATUS_CHANGED.name,
        payload=events.RunStatusChanged(
            run_id=str(run.id), from_status=from_status, to_status=to_status, reason=reason
        ),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run.id),
    )
    await session.flush()
    return run


async def start_run(
    session: AsyncSession,
    *,
    graph_key: str,
    graph_version: str,
    params: dict[str, Any],
    context: EventContext,
    dispatcher: Dispatcher,
) -> ResearchRun:
    await _refuse_if_killed(session)
    run_id = uuid7()
    run = ResearchRun(
        id=run_id,
        thread_id=f"run-{run_id}",
        graph_key=graph_key,
        graph_version=graph_version,
        params=params,
        status="queued",
        pause_requested=False,
        created_by=_actor_label(context),
        created_at=utcnow(),
        version=1,
    )
    session.add(run)
    await session.flush()
    await store.append(
        session,
        type_=events.RUN_CREATED.name,
        payload=events.RunCreated(
            run_id=str(run.id),
            thread_id=run.thread_id,
            graph_key=graph_key,
            graph_version=graph_version,
            params=params,
        ),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run.id),
    )
    await dispatcher.enqueue_run(session, run_id=run.id, thread_id=run.thread_id)
    return run


async def _record_request(
    session: AsyncSession, event: str, run: ResearchRun, context: EventContext, reason: str | None
) -> None:
    await store.append(
        session,
        type_=event,
        payload=events.RunControlRequested(run_id=str(run.id), reason=reason),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run.id),
    )


async def request_pause(
    session: AsyncSession, run_id: uuid.UUID, *, context: EventContext, reason: str | None = None
) -> ResearchRun:
    """Queued: paused at once. Running: the runner drains it after the current node (C-1)."""
    run = await lock_run(session, run_id)
    if run.status == "paused" or (run.status == "running" and run.pause_requested):
        return run  # already paused or pausing
    if run.status == "queued":
        await _record_request(session, events.PAUSE_REQUESTED.name, run, context, reason)
        return await transition(session, run, "paused", context=context, reason=reason)
    if run.status == "running":
        await _record_request(session, events.PAUSE_REQUESTED.name, run, context, reason)
        run.pause_requested = True
        run.version += 1
        await _notify(session, "pause", run.id)
        await session.flush()
        return run
    raise ConflictError(f"a {run.status} run cannot be paused", details={"run_id": str(run.id)})


async def resume(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    context: EventContext,
    dispatcher: Dispatcher,
    reason: str | None = None,
) -> ResearchRun:
    """Paused → queued, and an execution is dispatched (continuing from the last checkpoint)."""
    await _refuse_if_killed(session)
    run = await lock_run(session, run_id)
    if run.status != "paused":
        raise ConflictError(f"only a paused run can be resumed (this one is {run.status})")
    await _record_request(session, events.RESUME_REQUESTED.name, run, context, reason)
    await transition(session, run, "queued", context=context, reason=reason)
    await dispatcher.enqueue_run(session, run_id=run.id, thread_id=run.thread_id)
    return run


async def request_stop(
    session: AsyncSession, run_id: uuid.UUID, *, context: EventContext, reason: str | None = None
) -> ResearchRun:
    """Idle runs stop at once. A running run becomes `cancel_requested` until its runner
    confirms (or its heartbeat expires): the dashboard shows the two states differently."""
    run = await lock_run(session, run_id)
    if run.status in {"stopped", "cancel_requested"}:
        return run
    if run.status in TERMINAL:
        raise ConflictError(
            f"a {run.status} run cannot be stopped", details={"run_id": str(run.id)}
        )
    await _record_request(session, events.STOP_REQUESTED.name, run, context, reason)
    run.stop_reason = reason or "stopped by request"
    if run.status in IDLE:
        return await transition(session, run, "stopped", context=context, reason=reason)
    run.cancel_requested_at = utcnow()
    await transition(session, run, "cancel_requested", context=context, reason=reason)
    await _notify(session, "stop", run.id)
    return run


async def get_kill_switch(session: AsyncSession) -> KillSwitch:
    row = await session.scalar(select(SystemState).where(SystemState.id == 1))
    if row is None:
        raise RuntimeError("system_state row is missing")
    return KillSwitch(engaged=row.kill_switch, reason=row.reason, changed_by=row.changed_by)


async def engage_kill_switch(
    session: AsyncSession, *, reason: str, context: EventContext
) -> tuple[int, int]:
    """Stop every run and refuse new ones until released. Survives restarts (stored in the DB).

    Returns (runs stopped immediately, runs whose runner was asked to cancel).
    """
    state = await _kill_switch_row(session, exclusive=True)
    stopped = cancel_requested = 0
    active: Sequence[ResearchRun] = (
        await session.scalars(
            select(ResearchRun)
            .where(ResearchRun.status.not_in(TERMINAL | {"cancel_requested"}))
            .order_by(ResearchRun.created_at)
            .with_for_update()
        )
    ).all()
    for run in active:
        run.stop_reason = f"kill switch: {reason}"
        if run.status in IDLE:
            await transition(session, run, "stopped", context=context, reason="kill switch")
            stopped += 1
        else:
            run.cancel_requested_at = utcnow()
            await transition(
                session, run, "cancel_requested", context=context, reason="kill switch"
            )
            cancel_requested += 1
    state.kill_switch = True
    state.reason = reason
    state.changed_by = _actor_label(context)
    state.changed_at = utcnow()
    await store.append(
        session,
        type_=events.KILL_SWITCH_CHANGED.name,
        payload=events.KillSwitchChanged(
            engaged=True,
            reason=reason,
            runs_stopped=stopped,
            runs_cancel_requested=cancel_requested,
        ),
        context=context,
        aggregate_type="system",
        aggregate_id="kill_switch",
    )
    await _notify(session, "kill", None)
    await session.flush()
    return stopped, cancel_requested


async def release_kill_switch(session: AsyncSession, *, reason: str, context: EventContext) -> None:
    """Allow new runs again. Stopped runs stay stopped (stopped is terminal)."""
    state = await _kill_switch_row(session, exclusive=True)
    if not state.kill_switch:
        return
    state.kill_switch = False
    state.reason = reason
    state.changed_by = _actor_label(context)
    state.changed_at = utcnow()
    await store.append(
        session,
        type_=events.KILL_SWITCH_CHANGED.name,
        payload=events.KillSwitchChanged(
            engaged=False, reason=reason, runs_stopped=0, runs_cancel_requested=0
        ),
        context=context,
        aggregate_type="system",
        aggregate_id="kill_switch",
    )
    await session.flush()
