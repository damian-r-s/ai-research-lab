"""What every node of a run can reach: passed to LangGraph as the run's runtime context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: uuid.UUID
    thread_id: str
    factory: async_sessionmaker[AsyncSession]  # domain database (application role)
    runner_id: uuid.UUID | None = None
