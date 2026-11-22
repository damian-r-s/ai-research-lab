"""Budgets (ARCHITECTURE §7.5, criterion 2.8). Framework-free.

A budget limits what work may consume. In Phase 2 the measured resources are attempts and compute
seconds of agent tasks; tokens and external cost are added by the model layer (Phase 3).

Which budgets apply to an attempt: the run's own budget (`research_runs.budget_id`) and every
global budget (`global_day`, `global_month`) whose period contains the current time.

- `check` runs before every attempt: if any applicable budget is used up, `BudgetExceeded`.
- `commit_attempt` runs after every attempt and appends one ledger entry per applicable budget.

Limit: the check and the commit are not one atomic reservation, so parallel tasks of a run can
overrun a budget by at most the run's parallelism (one attempt each). Phase 3 adds reservations
for tokens and cost, where an overrun would cost money.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.runs import Budget, BudgetLedgerEntry, ResearchRun
from src.core.events import store
from src.core.events.store import EventContext
from src.core.ids import uuid7
from src.runs import events

ATTEMPT_REF = "task_attempt"


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int | None = Field(default=None, ge=1)
    max_seconds: float | None = Field(default=None, gt=0)
    max_parallel: int | None = Field(default=None, ge=1)


@dataclass(frozen=True, slots=True)
class Usage:
    attempts: int
    seconds: float


class BudgetExceeded(Exception):
    def __init__(self, budget_id: uuid.UUID, usage: Usage, limits: BudgetLimits) -> None:
        super().__init__(f"budget {budget_id} exhausted: {usage} of {limits}")
        self.budget_id, self.usage, self.limits = budget_id, usage, limits

    def proposal(self) -> dict[str, Any]:
        """Suggested extension: each exhausted limit doubled (the person may approve or not)."""
        doubled = {
            "max_attempts": self.limits.max_attempts * 2 if self.limits.max_attempts else None,
            "max_seconds": self.limits.max_seconds * 2 if self.limits.max_seconds else None,
        }
        return {k: v for k, v in doubled.items() if v is not None}


def limits_of(budget: Budget) -> BudgetLimits:
    return BudgetLimits(
        max_attempts=budget.limit_attempts,
        max_seconds=float(budget.limit_seconds) if budget.limit_seconds is not None else None,
        max_parallel=budget.limit_parallel,
    )


async def create_run_budget(session: AsyncSession, limits: BudgetLimits) -> Budget:
    budget = Budget(
        id=uuid7(),
        scope_type="run",
        limit_attempts=limits.max_attempts,
        limit_seconds=Decimal(str(limits.max_seconds)) if limits.max_seconds else None,
        limit_parallel=limits.max_parallel,
        created_at=utcnow(),
    )
    session.add(budget)
    await session.flush()
    return budget


async def create_global_budget(
    session: AsyncSession, *, scope: str, period_start: datetime, limits: BudgetLimits
) -> Budget:
    if scope not in {"global_day", "global_month"}:
        raise ValueError(f"unsupported global budget scope {scope!r}")
    budget = Budget(
        id=uuid7(),
        scope_type=scope,
        period_start=period_start,
        limit_attempts=limits.max_attempts,
        limit_seconds=Decimal(str(limits.max_seconds)) if limits.max_seconds else None,
        limit_parallel=limits.max_parallel,
        created_at=utcnow(),
    )
    session.add(budget)
    await session.flush()
    return budget


def _period_end(budget: Budget) -> datetime | None:
    if budget.period_start is None:
        return None
    if budget.scope_type == "global_day":
        return budget.period_start + timedelta(days=1)
    start = budget.period_start
    return start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)


async def applicable(session: AsyncSession, run_id: uuid.UUID) -> list[Budget]:
    run_budget = select(ResearchRun.budget_id).where(ResearchRun.id == run_id).scalar_subquery()
    now = utcnow()
    rows = (
        await session.scalars(
            select(Budget).where(
                or_(
                    Budget.id == run_budget,
                    Budget.scope_type.in_(["global_day", "global_month"])
                    & (Budget.period_start <= now),
                )
            )
        )
    ).all()
    return [
        b for b in rows if b.scope_type == "run" or (end := _period_end(b)) is None or now < end
    ]


async def usage(session: AsyncSession, budget_id: uuid.UUID) -> Usage:
    row = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(BudgetLedgerEntry.seconds), 0)).where(
                BudgetLedgerEntry.budget_id == budget_id,
                BudgetLedgerEntry.entry == "commit",
                BudgetLedgerEntry.ref_type == ATTEMPT_REF,
            )
        )
    ).one()
    return Usage(attempts=int(row[0]), seconds=float(row[1]))


def _exhausted(used: Usage, limits: BudgetLimits) -> bool:
    return (limits.max_attempts is not None and used.attempts >= limits.max_attempts) or (
        limits.max_seconds is not None and used.seconds >= limits.max_seconds
    )


async def check(session: AsyncSession, run_id: uuid.UUID) -> None:
    for budget in await applicable(session, run_id):
        used, limits = await usage(session, budget.id), limits_of(budget)
        if _exhausted(used, limits):
            raise BudgetExceeded(budget.id, used, limits)


async def parallel_limit(session: AsyncSession, run_id: uuid.UUID) -> int | None:
    limits = [b.limit_parallel for b in await applicable(session, run_id) if b.limit_parallel]
    return min(limits) if limits else None


async def commit_attempt(
    session: AsyncSession, run_id: uuid.UUID, attempt_id: uuid.UUID, seconds: float
) -> None:
    now = utcnow()
    for budget in await applicable(session, run_id):
        session.add(
            BudgetLedgerEntry(
                id=uuid7(),
                budget_id=budget.id,
                ref_type=ATTEMPT_REF,
                ref_id=str(attempt_id),
                entry="commit",
                tokens=0,
                cost=Decimal(0),
                seconds=Decimal(str(round(seconds, 3))),
                created_at=now,
            )
        )
    await session.flush()


async def record_exceeded(
    session: AsyncSession, run_id: uuid.UUID, exc: BudgetExceeded, *, context: EventContext
) -> None:
    await store.append(
        session,
        type_=events.BUDGET_EXCEEDED.name,
        payload=events.BudgetExceededEvent(
            run_id=str(run_id),
            budget_id=str(exc.budget_id),
            used_attempts=exc.usage.attempts,
            used_seconds=exc.usage.seconds,
            limits=exc.limits.model_dump(exclude_none=True),
        ),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run_id),
    )


async def extend(
    session: AsyncSession,
    budget_id: uuid.UUID,
    new_limits: dict[str, Any],
    *,
    run_id: uuid.UUID,
    context: EventContext,
) -> Budget:
    """Raise a budget's limits (only upwards). Called when a budget extension is approved."""
    budget = await session.get(Budget, budget_id, with_for_update=True)
    if budget is None:
        raise ValueError(f"budget {budget_id} does not exist")
    proposed = BudgetLimits.model_validate(new_limits)
    if proposed.max_attempts and (budget.limit_attempts or 0) < proposed.max_attempts:
        budget.limit_attempts = proposed.max_attempts
    if proposed.max_seconds and float(budget.limit_seconds or 0) < proposed.max_seconds:
        budget.limit_seconds = Decimal(str(proposed.max_seconds))
    await store.append(
        session,
        type_=events.BUDGET_EXTENDED.name,
        payload=events.BudgetExtended(
            run_id=str(run_id),
            budget_id=str(budget_id),
            limits=limits_of(budget).model_dump(exclude_none=True),
        ),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run_id),
    )
    await session.flush()
    return budget
