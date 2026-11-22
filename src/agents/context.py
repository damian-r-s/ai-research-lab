"""What every node of a run can reach: passed to LangGraph as the run's runtime context."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.runs.tasks import TaskLimits


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: uuid.UUID
    thread_id: str
    factory: async_sessionmaker[AsyncSession]  # domain database (application role)
    runner_id: uuid.UUID | None = None
    limits: TaskLimits = field(default_factory=TaskLimits)  # default per-task limits
    # Tasks of this run executing at once (criterion 2.8); None = no limit.
    slots: asyncio.Semaphore | None = None
