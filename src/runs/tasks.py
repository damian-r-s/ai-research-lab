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
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
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
from src.runs import budgets, events

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
    budget_check: bool = False,
) -> tuple[AgentTask, AgentTaskAttempt | None, budgets.BudgetExceeded | None]:
    """Find or create the task and open its next attempt.

    Returns (task, None, None) if the task already succeeded, and (task, None, exceeded) if a
    budget is used up; the task is then marked `blocked_budget` and the event recorded here, in
    this transaction, so the caller only has to raise after commit.
    """
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
        return task, None, None
    if budget_check:
        try:
            await budgets.check(session, run_id)
        except budgets.BudgetExceeded as exc:
            task.status = "blocked_budget"
            await budgets.record_exceeded(
                session, run_id, exc, context=EventContext(Actor("agent", node_path))
            )
            return task, None, exc
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
    return task, attempt, None


class TransientTaskError(Exception):
    """A failure worth retrying (rate limit, temporary outage). Timeouts count as transient."""


class PermanentTaskError(Exception):
    """A failure that a retry cannot fix. Unknown exceptions are treated the same way."""


@dataclass(frozen=True, slots=True)
class TaskLimits:
    """Per-task limits (criteria 2.7, 2.8). `max_retries` counts retries, not runs (C-8)."""

    timeout_s: float = 600.0
    max_retries: int = 2
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0

    def backoff(self, retry: int) -> float:
        """Delay before retry number `retry` (1-based): exponential, capped."""
        return float(min(self.backoff_max_s, self.backoff_initial_s * 2 ** (retry - 1)))

    def as_record(self) -> dict[str, Any]:
        return {"timeout_s": self.timeout_s, "max_retries": self.max_retries}


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
    """Close an attempt, update its task, commit its usage to the budgets, audit it: one tx."""
    now = utcnow()
    async with session_scope(factory) as session:
        task = await session.get(AgentTask, task_id, with_for_update=True)
        attempt = await session.get(AgentTaskAttempt, attempt_id, with_for_update=True)
        assert task is not None and attempt is not None
        attempt.outcome, attempt.ended_at, attempt.error = outcome, now, error
        task.status, task.error = task_status, error
        seconds = (now - attempt.started_at).total_seconds()
        task.compute_seconds = Decimal(task.compute_seconds or 0) + Decimal(str(round(seconds, 3)))
        if result is not None:
            task.result_refs = result
        if task_status in {"succeeded", "failed", "cancelled"}:
            task.ended_at = now
        await budgets.commit_attempt(session, task.run_id, attempt.id, seconds)
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


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


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
    limits: TaskLimits | None = None,
    slots: asyncio.Semaphore | None = None,
) -> Result:
    """Run `work` as the logical task at this graph position, at most once successfully.

    Each attempt: budget check → parallelism slot → work under the time limit → recorded outcome.
    Transient errors and timeouts are retried with backoff up to `limits.max_retries`; any other
    error fails the task at once. A used-up budget raises `BudgetExceeded` (task `blocked_budget`).
    """
    limits = limits or TaskLimits()
    key = task_key(thread_id, node_path, fanout_key)

    async def attempt_once(retry: int) -> tuple[bool, Result]:
        """One attempt. Returns (True, result) when done, (False, {}) when it should be retried;
        raises for a used-up budget, a permanent failure or the last failed retry."""
        async with session_scope(factory) as session:
            task, attempt, exceeded = await _open_attempt(
                session,
                run_id=run_id,
                key=key,
                node_path=node_path,
                runner_id=runner_id,
                role_key=role_key,
                goal=goal,
                limits=limits.as_record(),
                budget_check=True,
            )
            result_refs = dict(task.result_refs or {})
            task_id = task.id
            attempt_id = attempt.id if attempt is not None else None
        if exceeded is not None:
            raise exceeded  # recorded and committed above: task blocked_budget, event appended
        if attempt_id is None:  # finished before a crash: reuse, never redo
            return True, result_refs
        try:
            async with asyncio.timeout(limits.timeout_s):
                result = await work()
        except asyncio.CancelledError:
            # A stop: record it even though this task is being cancelled, then keep cancelling.
            await asyncio.shield(
                _close(factory, task_id, attempt_id, outcome="cancelled", task_status="cancelled")
            )
            raise
        except (TransientTaskError, TimeoutError) as exc:
            timed_out = isinstance(exc, TimeoutError)
            final = retry >= limits.max_retries
            await _close(
                factory,
                task_id,
                attempt_id,
                outcome="timed_out" if timed_out else "failed",
                task_status="failed" if final else "running",
                error=f"timed out after {limits.timeout_s} s" if timed_out else _error_text(exc),
            )
            if final:
                raise
            return False, {}
        except Exception as exc:
            await _close(
                factory,
                task_id,
                attempt_id,
                outcome="failed",
                task_status="failed",
                error=_error_text(exc),
            )
            raise
        await _close(
            factory,
            task_id,
            attempt_id,
            outcome="succeeded",
            task_status="succeeded",
            result=result,
        )
        return True, result

    retry = 0
    while True:
        # The slot covers the attempt only (its record, budget check and work), not the backoff.
        async with slots if slots is not None else contextlib.nullcontext():
            done, result = await attempt_once(retry)
        if done:
            return result
        retry += 1
        await asyncio.sleep(limits.backoff(retry))
