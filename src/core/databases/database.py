"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import DatabaseSettings


def create_engine(settings: DatabaseSettings, *, url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.url.get_secret_value(),
        pool_size=settings.pool_size,
        pool_timeout=settings.pool_timeout_s,
        pool_pre_ping=True,
        connect_args={
            "options": f"-c statement_timeout={settings.statement_timeout_ms} -c timezone=UTC",
            "application_name": "ai-research-lab",
        },
    )


def libpq_url(sqlalchemy_url: str) -> str:
    """Convert a `postgresql+psycopg://` SQLAlchemy URL into a plain libpq URL (for psycopg)."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit (we return them from services).
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Transactional scope: commit on success, roll back on any exception."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
