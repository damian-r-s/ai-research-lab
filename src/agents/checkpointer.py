"""LangGraph's PostgreSQL checkpointer on its own role and schema (DECISIONS C-6, C-7).

The library does not qualify its table names, so every connection sets `search_path=langgraph`.
The role in `database.graph_url` (default `lab_graph`) can use only that schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.core.config import DatabaseSettings
from src.core.databases.database import libpq_url

CHECKPOINT_SCHEMA = "langgraph"


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
