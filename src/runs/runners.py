"""Runner processes as recorded in `lab.runners` (heartbeats; shown in the dashboard)."""

from __future__ import annotations

import os
import socket
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.runs import Runner


async def register(
    session: AsyncSession, runner_id: uuid.UUID, *, capabilities: dict[str, Any]
) -> Runner:
    now = utcnow()
    runner = Runner(
        id=runner_id,
        hostname=socket.gethostname(),
        pid=os.getpid(),
        status="active",
        capabilities=capabilities,
        started_at=now,
        heartbeat_at=now,
    )
    session.add(runner)
    await session.flush()
    return runner


async def heartbeat(session: AsyncSession, runner_id: uuid.UUID) -> None:
    await session.execute(
        update(Runner)
        .where(Runner.id == runner_id, Runner.status.in_(["active", "stopping"]))
        .values(heartbeat_at=utcnow())
    )


async def set_status(session: AsyncSession, runner_id: uuid.UUID, status: str) -> None:
    await session.execute(
        update(Runner).where(Runner.id == runner_id).values(status=status, heartbeat_at=utcnow())
    )
