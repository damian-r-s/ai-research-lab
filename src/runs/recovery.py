"""Recovery after a runner died (DECISIONS C-3; ARCHITECTURE §14). Framework-free.

The stalled-job reaper (src.services.runner) finds jobs whose worker stopped sending heartbeats.
For each such run it calls `recover_lost_run` before retrying the job, and it calls
`mark_dead_runners` to close the attempts the dead runners left open.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.runs import AgentTaskAttempt, Runner
from src.core.errors import NotFound
from src.core.events.store import EventContext
from src.runs import service


async def recover_lost_run(
    session: AsyncSession, run_id: uuid.UUID, *, context: EventContext
) -> str | None:
    """Hand a run back after its runner died. Returns its new status, or None if unchanged.

    running → queued: the retried job continues it from its last checkpoint.
    cancel_requested → stopped: the stop is complete; the runner that would confirm it is gone.
    """
    try:
        run = await service.lock_run(session, run_id)
    except NotFound:
        return None
    if run.status == "running":
        run.runner_id = None
        await service.transition(session, run, "queued", context=context, reason="runner lost")
        return run.status
    if run.status == "cancel_requested":
        run.runner_id = None
        await service.transition(session, run, "stopped", context=context, reason="runner lost")
        return run.status
    return None


async def mark_dead_runners(session: AsyncSession, *, stale_before: datetime) -> list[uuid.UUID]:
    """Runners silent since `stale_before` are dead; their open attempts become `abandoned`."""
    dead = list(
        (
            await session.scalars(
                update(Runner)
                .where(
                    Runner.status.in_(["active", "stopping"]), Runner.heartbeat_at < stale_before
                )
                .values(status="dead")
                .returning(Runner.id)
            )
        ).all()
    )
    if dead:
        await session.execute(
            update(AgentTaskAttempt)
            .where(AgentTaskAttempt.runner_id.in_(dead), AgentTaskAttempt.outcome.is_(None))
            .values(outcome="abandoned", ended_at=utcnow(), error="runner died")
        )
    return dead


async def live_runner_ids(session: AsyncSession) -> set[uuid.UUID]:
    return set((await session.scalars(select(Runner.id).where(Runner.status == "active"))).all())
