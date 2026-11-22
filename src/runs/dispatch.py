"""Run dispatch through Procrastinate (DECISIONS D-03), hidden behind a small interface so the
transport can change without touching workflows (ARCHITECTURE §7.4).

Jobs are enqueued on the caller's own connection, inside its transaction: the job exists if and
only if the run change that needs it is committed (spike A-7).

Locks (spike A-7):
- `lock = thread:<thread_id>`: at most one job per LangGraph thread runs at a time, on any runner.
  A job whose runner died keeps the lock until the reaper (C-3) retries it.
- `queueing_lock = run:<run_id>`: at most one *waiting* job per run, so a repeated resume does not
  queue a second execution.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import DatabaseSettings
from src.core.databases.database import libpq_url

JOBS_SCHEMA = "jobs"
RUN_QUEUE = "runs"
EXECUTE_RUN_TASK = "lab.execute_run"


def thread_lock(thread_id: str) -> str:
    return f"thread:{thread_id}"


def run_queueing_lock(run_id: uuid.UUID) -> str:
    return f"run:{run_id}"


class Dispatcher(Protocol):
    async def enqueue_run(
        self, session: AsyncSession, *, run_id: uuid.UUID, thread_id: str
    ) -> bool:
        """Queue an execution of the run in the session's transaction.

        Returns False if an execution of this run is already waiting (nothing new was queued).
        """
        ...


def make_job_app(settings: DatabaseSettings) -> procrastinate.App:
    """The Procrastinate app for this process. Its own pool (used by workers) runs as the
    application role with `search_path=jobs` (DECISIONS C-6)."""
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=libpq_url(settings.url.get_secret_value()),
            kwargs={"options": f"-c search_path={JOBS_SCHEMA}"},
        )
    )


class ProcrastinateDispatcher:
    def __init__(self, app: procrastinate.App) -> None:
        self._app = app

    async def enqueue_run(
        self, session: AsyncSession, *, run_id: uuid.UUID, thread_id: str
    ) -> bool:
        conn = await session.connection()
        previous = await conn.scalar(text("SELECT current_setting('search_path')"))
        try:
            # Savepoint: a duplicate (unique violation) must not abort the caller's transaction.
            async with conn.begin_nested():
                # Procrastinate's SQL is unqualified: point the insert at `jobs` (C-6).
                await conn.execute(text(f"SET LOCAL search_path = {JOBS_SCHEMA}"))
                raw = (await conn.get_raw_connection()).driver_connection
                await self._app.configure_task(
                    EXECUTE_RUN_TASK,
                    queue=RUN_QUEUE,
                    lock=thread_lock(thread_id),
                    queueing_lock=run_queueing_lock(run_id),
                    connection=raw,
                ).defer_async(run_id=str(run_id))
        except AlreadyEnqueued:
            return False
        finally:
            await conn.execute(
                text("SELECT set_config('search_path', :value, true)"), {"value": previous}
            )
        return True
