"""LangGraph's PostgreSQL checkpointer on its own role and schema (DECISIONS C-6, C-7).

The library does not qualify its table names, so every connection sets `search_path=langgraph`.
The role in `database.graph_url` (default `lab_graph`) can use only that schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.clock import utcnow
from src.core.config import DatabaseSettings
from src.core.databases.database import libpq_url, session_scope
from src.core.databases.models.runs import ResearchRun
from src.runs.states import TERMINAL

CHECKPOINT_SCHEMA = "langgraph"

# The checkpointer type, for code outside src.agents (which must not import LangGraph).
Checkpointer = AsyncPostgresSaver


def _graph_dsn(settings: DatabaseSettings) -> str:
    if settings.graph_url is None:
        raise RuntimeError("database.graph_url is not configured (LangGraph checkpointer role)")
    return libpq_url(settings.graph_url.get_secret_value())


def _connect_kwargs(settings: DatabaseSettings) -> dict[str, Any]:
    # autocommit + no prepared statements + dict rows: what the library requires.
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": (
            f"-c search_path={CHECKPOINT_SCHEMA} "
            f"-c statement_timeout={settings.statement_timeout_ms} -c timezone=UTC"
        ),
        "application_name": "ai-research-lab-checkpointer",
    }


async def setup_checkpointer(settings: DatabaseSettings, *, readers: Sequence[str] = ()) -> None:
    """Create or migrate the checkpointer's tables (idempotent). Run by `scripts.migrate`.

    `readers` get SELECT on every checkpoint table (the backup role: `pg_dump` runs as the schema
    owner, which otherwise cannot read tables owned by the graph role). Re-granted on every run,
    so tables added by a library upgrade are covered too.
    """
    async with await psycopg.AsyncConnection.connect(
        _graph_dsn(settings), **_connect_kwargs(settings)
    ) as conn:
        await AsyncPostgresSaver(conn).setup()
        for role in readers:
            await conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                    sql.Identifier(CHECKPOINT_SCHEMA), sql.Identifier(role)
                )
            )


@asynccontextmanager
async def open_checkpointer(
    settings: DatabaseSettings, *, max_size: int = 4
) -> AsyncIterator[AsyncPostgresSaver]:
    """A pooled checkpointer for a runner process (several runs share the pool)."""
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        _graph_dsn(settings),
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs=_connect_kwargs(settings),
    )
    await pool.open(wait=True)
    try:
        yield AsyncPostgresSaver(pool)
    finally:
        await pool.close()


async def prune_finished_runs(
    saver: AsyncPostgresSaver,
    factory: async_sessionmaker[AsyncSession],
    *,
    older_than: timedelta,
    batch: int = 100,
) -> int:
    """Reduce the checkpoints of runs finished longer than `older_than` ago to the latest one.

    The domain record is never touched apart from `checkpoints_pruned_at` (criterion 2.11), and
    the final state stays readable. Returns how many runs were pruned.
    """
    cutoff = utcnow() - older_than
    async with factory() as session:
        runs = (
            await session.scalars(
                select(ResearchRun)
                .where(
                    ResearchRun.status.in_(TERMINAL),
                    ResearchRun.ended_at < cutoff,
                    ResearchRun.checkpoints_pruned_at.is_(None),
                )
                .order_by(ResearchRun.ended_at)
                .limit(batch)
            )
        ).all()
        threads = {r.id: r.thread_id for r in runs}
    if not threads:
        return 0
    await saver.aprune(list(threads.values()), strategy="keep_latest")
    async with session_scope(factory) as session:
        await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id.in_(list(threads)))
            .values(checkpoints_pruned_at=utcnow())
        )
    return len(threads)
