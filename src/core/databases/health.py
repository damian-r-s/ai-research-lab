"""Readiness check: database reachable and schema at the expected migration head."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.databases.base import SCHEMA
from src.core.databases.migrate import expected_heads


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    reachable: bool
    current_revisions: tuple[str, ...]
    expected_revisions: tuple[str, ...]
    error: str | None = None

    @property
    def migrations_current(self) -> bool:
        return set(self.current_revisions) == set(self.expected_revisions)

    @property
    def ready(self) -> bool:
        return self.reachable and self.migrations_current


async def check_database(engine: AsyncEngine) -> DatabaseHealth:
    expected = tuple(sorted(expected_heads()))
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            exists = await conn.scalar(
                text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"{SCHEMA}.alembic_version"}
            )
            current: tuple[str, ...] = ()
            if exists:
                rows = await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))  # noqa: S608 - constant schema name
                current = tuple(sorted(r[0] for r in rows))
        return DatabaseHealth(True, current, expected)
    except Exception as exc:  # any failure means "not ready"; details go to logs, not to clients
        return DatabaseHealth(False, (), expected, error=f"{type(exc).__name__}: {exc}")
