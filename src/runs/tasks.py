"""Agent task records (spec §4.2, ARCHITECTURE §6.3 step 2 and §6.7). Framework-free.

One logical task per graph position, identified by `{thread_id}:{node_path}:{fanout_key}`.
LangGraph re-executes a node that was running when its process died; `run_task` makes that safe:

- a task that already succeeded returns its stored result, and the work is not repeated;
- otherwise a new attempt is recorded. Any attempt still open for that task belongs to an
  execution that no longer exists (one execution per thread at a time, I-24), so it is marked
  `abandoned`, never erased.

Each step commits on its own, so the record survives a crash in the middle of the work.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.clock import utcnow
from src.core.databases.database import session_scope
from src.core.databases.models.runs import AgentTask, AgentTaskAttempt
from src.core.events import store
from src.core.events.store import Actor, EventContext
from src.core.ids import uuid7
from src.runs import events

Result = dict[str, Any]


def task_key(thread_id: str, node_path: str, fanout_key: str | None = None) -> str:
    return f"{thread_id}:{node_path}:{fanout_key or ''}"


async def _open_attempt(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    key: str,
    node_path: str,
    runner_id: uuid.UUID | None,
    role_key: str | None,
    goal: str | None,
    limits: dict[str, Any],
) -> tuple[AgentTask, AgentTaskAttempt | None]:
    now = utcnow()
    await session.execute(
        insert(AgentTask)
        .values(
            id=uuid7(),
            run_id=run_id,
            idempotency_key=key,
            node_path=node_path,
            role_key=role_key,
            goal=goal,
            input_refs={},
            limits=limits,
            status="pending",
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[AgentTask.idempotency_key])
    )
    task = await session.scalar(
        select(AgentTask).where(AgentTask.idempotency_key == key).with_for_update()
    )
    assert task is not None
    if task.status == "succeeded":
        return task, None
    await session.execute(
        update(AgentTaskAttempt)
        .where(AgentTaskAttempt.agent_task_id == task.id, AgentTaskAttempt.outcome.is_(None))
        .values(outcome="abandoned", ended_at=now, error="execution ended without finishing")
    )
    last = await session.scalar(
        select(func.max(AgentTaskAttempt.attempt_no)).where(
            AgentTaskAttempt.agent_task_id == task.id
        )
    )
    attempt = AgentTaskAttempt(
        id=uuid7(),
        agent_task_id=task.id,
        attempt_no=(last or 0) + 1,
        runner_id=runner_id,
        started_at=now,
    )
    session.add(attempt)
    task.status = "running"
    task.error = None
    await session.flush()
    return task, attempt


async def _close(
    factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    outcome: str,
    task_status: str,
    result: Result | None = None,
    error: str | None = None,
) -> None:
    now = utcnow()
    async with session_scope(factory) as session:
        task = await session.get(AgentTask, task_id, with_for_update=True)
        attempt = await session.get(AgentTaskAttempt, attempt_id, with_for_update=True)
        assert task is not None and attempt is not None
        attempt.outcome, attempt.ended_at, attempt.error = outcome, now, error
        task.status, task.error = task_status, error
        if result is not None:
            task.result_refs = result
        if task_status in {"succeeded", "failed", "cancelled"}:
            task.ended_at = now
        await store.append(
            session,
            type_=events.TASK_FINISHED.name,
            payload=events.TaskFinished(
                run_id=str(task.run_id),
                task_id=str(task.id),
                node_path=task.node_path,
                attempt_no=attempt.attempt_no,
                outcome=outcome,
                error=error,
            ),
            context=EventContext(Actor("agent", task.role_key or task.node_path)),
            aggregate_type="research_run",
            aggregate_id=str(task.run_id),
        )


async def run_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    thread_id: str,
    node_path: str,
    work: Callable[[], Awaitable[Result]],
    fanout_key: str | None = None,
    runner_id: uuid.UUID | None = None,
    role_key: str | None = None,
    goal: str | None = None,
    limits: dict[str, Any] | None = None,
) -> Result:
    """Run `work` as the logical task at this graph position, at most once successfully."""
    key = task_key(thread_id, node_path, fanout_key)
    async with session_scope(factory) as session:
        task, attempt = await _open_attempt(
            session,
            run_id=run_id,
            key=key,
            node_path=node_path,
            runner_id=runner_id,
            role_key=role_key,
            goal=goal,
            limits=limits or {},
        )
        if attempt is None:  # finished before a crash: reuse, never redo
            return dict(task.result_refs or {})
        task_id, attempt_id = task.id, attempt.id
    try:
        result = await work()
    except asyncio.CancelledError:
        # A stop: record it even though this task is being cancelled, then keep cancelling.
        await asyncio.shield(
            _close(factory, task_id, attempt_id, outcome="cancelled", task_status="cancelled")
        )
        raise
    except Exception as exc:
        await _close(
            factory,
            task_id,
            attempt_id,
            outcome="failed",
            task_status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    await _close(
        factory, task_id, attempt_id, outcome="succeeded", task_status="succeeded", result=result
    )
    return result
