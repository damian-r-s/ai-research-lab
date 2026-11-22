"""Phase 2 step 2b.5: retries, time and parallelism limits, budgets (criteria 2.7 and 2.8)."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents.checkpointer import open_checkpointer, setup_checkpointer
from src.agents.graphs import selftest
from src.agents.runtime import Outcome, RunExecution
from src.core.config import LimitSettings, Settings
from src.core.databases.database import create_engine, create_session_factory, session_scope
from src.core.databases.models.runs import (
    AgentTask,
    AgentTaskAttempt,
    Approval,
    Budget,
    ResearchRun,
)
from src.core.events.store import Actor, EventContext
from src.runs import approvals, budgets, service
from src.runs.budgets import BudgetLimits
from src.runs.dispatch import ProcrastinateDispatcher, make_job_app
from test.conftest import ScratchDatabase, settings_for, sql

pytestmark = pytest.mark.integration

USER = EventContext(Actor("user", "tester"))
FAST = LimitSettings(backoff_initial_s=0.2, backoff_max_s=1.0)

Attempt = tuple[str | None, datetime, datetime | None]


class Lab:
    def __init__(
        self, db: ScratchDatabase, settings: Settings, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.db, self.settings, self.factory = db, settings, factory
        self.dispatcher = ProcrastinateDispatcher(make_job_app(settings.database))

    async def start(self, budget: BudgetLimits | None = None, **params: Any) -> uuid.UUID:
        async with session_scope(self.factory) as s:
            run = await service.start_run(
                s,
                graph_key=selftest.KEY,
                graph_version=selftest.VERSION,
                params=params,
                context=USER,
                dispatcher=self.dispatcher,
                budget=budget,
            )
            return run.id

    async def execute(self, run_id: uuid.UUID, limits: LimitSettings = FAST) -> Outcome:
        async with open_checkpointer(self.settings.database) as saver:
            return await RunExecution(
                run_id, factory=self.factory, checkpointer=saver, limits=limits
            ).execute()

    async def run(self, run_id: uuid.UUID) -> ResearchRun:
        async with self.factory() as s:
            run = await s.get(ResearchRun, run_id)
            assert run is not None
            return run

    async def attempts(self, run_id: uuid.UUID) -> dict[str, list[Attempt]]:
        async with self.factory() as s:
            rows = (
                await s.execute(
                    select(AgentTask.idempotency_key, AgentTaskAttempt)
                    .join(AgentTaskAttempt, AgentTaskAttempt.agent_task_id == AgentTask.id)
                    .where(AgentTask.run_id == run_id)
                    .order_by(AgentTaskAttempt.attempt_no)
                )
            ).all()
        result: dict[str, list[Attempt]] = {}
        for key, a in rows:
            name = key.split(":", 1)[1].rstrip(":")
            result.setdefault(name, []).append((a.outcome, a.started_at, a.ended_at))
        return result

    async def task_status(self, run_id: uuid.UUID, name: str) -> str:
        async with self.factory() as s:
            status = await s.scalar(
                select(AgentTask.status).where(
                    AgentTask.run_id == run_id,
                    AgentTask.idempotency_key == f"run-{run_id}:{name}",
                )
            )
        assert status is not None
        return status

    async def approvals(self, run_id: uuid.UUID) -> list[Approval]:
        async with self.factory() as s:
            return list(
                (
                    await s.scalars(
                        select(Approval)
                        .where(Approval.run_id == run_id)
                        .order_by(Approval.created_at)
                    )
                ).all()
            )

    async def decide(self, approval_id: uuid.UUID, approved: bool) -> None:
        async with session_scope(self.factory) as s:
            await approvals.decide(
                s,
                approval_id,
                approved=approved,
                comment=None,
                context=USER,
                dispatcher=self.dispatcher,
            )

    def events(self) -> list[str]:
        return [
            str(t) for (t,) in sql(self.db.admin_url, "SELECT type FROM lab.events ORDER BY seq")
        ]


@pytest_asyncio.fixture
async def lab(fresh_db: ScratchDatabase) -> AsyncIterator[Lab]:
    settings = settings_for(fresh_db)
    await setup_checkpointer(settings.database)
    engine = create_engine(settings.database)
    try:
        yield Lab(fresh_db, settings, create_session_factory(engine))
    finally:
        await engine.dispose()


def outcomes(attempts: list[Attempt]) -> list[str | None]:
    return [a[0] for a in attempts]


def max_concurrency(attempts: list[Attempt]) -> int:
    points = sorted(
        [(start, 1) for _, start, _ in attempts]
        + [(end, -1) for _, _, end in attempts if end is not None],
        key=lambda p: (p[0], p[1]),  # an end at the same instant comes before a start
    )
    current = peak = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


# --- 2.7: retries ----------------------------------------------------------------------------


async def test_transient_failures_are_retried_with_backoff(lab: Lab) -> None:
    run_id = await lab.start(steps=1, transient_failures_at_step=1, transient_failures=2)
    assert await lab.execute(run_id) is Outcome.COMPLETED
    step = (await lab.attempts(run_id))["step:1"]
    assert outcomes(step) == ["failed", "failed", "succeeded"]
    gaps = [b[1] - a[2] for a, b in itertools.pairwise(step) if a[2] is not None]
    assert gaps[0] >= timedelta(seconds=0.2) and gaps[1] >= timedelta(seconds=0.4)  # exponential


async def test_retries_stop_at_the_limit(lab: Lab) -> None:
    run_id = await lab.start(steps=1, transient_failures_at_step=1, transient_failures=10)
    assert await lab.execute(run_id) is Outcome.FAILED
    assert outcomes((await lab.attempts(run_id))["step:1"]) == ["failed"] * 3  # 1 + max_retries 2
    assert await lab.task_status(run_id, "step:1") == "failed"
    assert "transient failure 3" in ((await lab.run(run_id)).error or "")


async def test_permanent_failures_are_not_retried(lab: Lab) -> None:
    run_id = await lab.start(steps=1, fail_at_step=1)
    limits = LimitSettings(max_retries=5, backoff_initial_s=0)
    assert await lab.execute(run_id, limits) is Outcome.FAILED
    assert outcomes((await lab.attempts(run_id))["step:1"]) == ["failed"]


# --- 2.8: time and parallelism limits --------------------------------------------------------


async def test_a_task_over_its_time_limit_is_timed_out_and_retried(lab: Lab) -> None:
    run_id = await lab.start(steps=1, hang_at_step=1, task_timeout_s=0.3)
    limits = LimitSettings(max_retries=1, backoff_initial_s=0)
    started = datetime.now(UTC)
    assert await lab.execute(run_id, limits) is Outcome.FAILED
    assert datetime.now(UTC) - started < timedelta(seconds=5)
    step = (await lab.attempts(run_id))["step:1"]
    assert outcomes(step) == ["timed_out", "timed_out"]
    assert all(end is not None and end - start < timedelta(seconds=1) for _, start, end in step)


@pytest.mark.parametrize(
    ("limits", "budget", "expected"),
    [
        (LimitSettings(run_parallelism=2), None, 2),
        (LimitSettings(run_parallelism=4), BudgetLimits(max_parallel=1), 1),
    ],
)
async def test_parallelism_is_limited(
    lab: Lab, limits: LimitSettings, budget: BudgetLimits | None, expected: int
) -> None:
    run_id = await lab.start(budget=budget, steps=1, fanout=6, delay_s=0.3)
    assert await lab.execute(run_id, limits) is Outcome.COMPLETED
    attempts = await lab.attempts(run_id)
    probes = [a for name, v in attempts.items() if name.startswith("probe") for a in v]
    assert len(probes) == 6
    assert max_concurrency(probes) == expected  # at the limit, never above it


# --- 2.8: budgets ------------------------------------------------------------------------------


async def test_an_exhausted_budget_blocks_the_task_and_asks_for_an_extension(lab: Lab) -> None:
    run_id = await lab.start(budget=BudgetLimits(max_attempts=3), steps=5)
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    # plan, step 1, step 2 used the 3 attempts; step 3 is blocked before it starts.
    assert await lab.task_status(run_id, "step:3") == "blocked_budget"
    assert "step:3" not in await lab.attempts(run_id)
    assert "runs.budget.exceeded" in lab.events()
    (request,) = await lab.approvals(run_id)
    assert request.kind == "budget_extension"
    assert request.request_payload["used"]["attempts"] == 3
    assert request.request_payload["proposed_limits"] == {"max_attempts": 6}
    run = await lab.run(run_id)
    assert (run.status, run.runner_id) == ("awaiting_approval", None)


async def test_an_approved_extension_raises_the_budget_and_the_run_continues(lab: Lab) -> None:
    run_id = await lab.start(budget=BudgetLimits(max_attempts=3), steps=5)
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    (first,) = await lab.approvals(run_id)
    await lab.decide(first.id, approved=True)
    run = await lab.run(run_id)
    async with lab.factory() as s:
        budget = await s.get(Budget, run.budget_id)
    assert budget is not None and budget.limit_attempts == 6  # raised in the decision itself
    assert "runs.budget.extended" in lab.events()

    # 8 tasks in total (plan, 5 steps, review, finish): the raised budget runs out again.
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    second = (await lab.approvals(run_id))[1]
    assert second.request_payload["used"]["attempts"] == 6
    await lab.decide(second.id, approved=True)
    assert await lab.execute(run_id) is Outcome.COMPLETED

    attempts = await lab.attempts(run_id)
    assert all(outcomes(v) == ["succeeded"] for v in attempts.values())  # nothing repeated
    assert len(await lab.approvals(run_id)) == 2  # one request per block, not per resume


async def test_a_rejected_extension_fails_the_run(lab: Lab) -> None:
    run_id = await lab.start(budget=BudgetLimits(max_attempts=1), steps=2)
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    (request,) = await lab.approvals(run_id)
    await lab.decide(request.id, approved=False)
    assert await lab.execute(run_id) is Outcome.FAILED
    assert "budget extension was rejected" in ((await lab.run(run_id)).error or "")


async def test_a_global_daily_budget_applies_only_in_its_period(lab: Lab) -> None:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_scope(lab.factory) as s:
        await budgets.create_global_budget(
            s,
            scope="global_day",
            period_start=today - timedelta(days=1),
            limits=BudgetLimits(max_attempts=1),
        )  # yesterday's: no longer applies
        current = await budgets.create_global_budget(
            s, scope="global_day", period_start=today, limits=BudgetLimits(max_attempts=2)
        )
    run_id = await lab.start(steps=3)
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    (request,) = await lab.approvals(run_id)
    assert request.request_payload["budget_id"] == str(current.id)
    assert request.request_payload["used"]["attempts"] == 2


async def test_every_attempt_is_committed_to_each_applicable_budget(lab: Lab) -> None:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_scope(lab.factory) as s:
        day = await budgets.create_global_budget(
            s, scope="global_day", period_start=today, limits=BudgetLimits(max_attempts=100)
        )
    run_id = await lab.start(budget=BudgetLimits(max_attempts=100), steps=2, delay_s=0.1)
    assert await lab.execute(run_id) is Outcome.COMPLETED
    run = await lab.run(run_id)
    assert run.budget_id is not None
    async with lab.factory() as s:
        run_usage = await budgets.usage(s, run.budget_id)
        day_usage = await budgets.usage(s, day.id)
    attempts = sum(len(v) for v in (await lab.attempts(run_id)).values())
    assert (
        run_usage.attempts == day_usage.attempts == attempts == 5
    )  # plan, 2 steps, review, finish
    assert run_usage.seconds >= 0.2  # the two steps' work time is measured
