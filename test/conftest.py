"""Shared fixtures. Integration tests run against a real PostgreSQL, never a mock.

Each test session creates throwaway databases in the local cluster (scripts/dev-db.sh) using the
superuser URL in LAB_TEST_ADMIN_URL, and drops them afterwards.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass

os.environ.setdefault("LAB_ENV", "test")

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.config import DatabaseSettings, Settings
from src.core.databases.database import create_engine, create_session_factory
from src.core.databases.migrate import upgrade


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.exit(f"{name} is not set; run `source scripts/env.sh` first", returncode=2)
    return value


@dataclass(frozen=True)
class ScratchDatabase:
    name: str
    app_url: str  # least-privilege application role
    owner_url: str  # schema owner (migrations)
    graph_url: str  # LangGraph checkpointer role, confined to schema langgraph
    admin_url: str  # superuser on this database: ONLY for tests that must bypass protections


@pytest.fixture(scope="session")
def make_database() -> Iterator[Callable[[], ScratchDatabase]]:
    """Factory for empty databases with the same role setup as the dev database."""
    admin_url = _require_env("LAB_TEST_ADMIN_URL")
    admin_template = make_url(admin_url)
    app_template = make_url(_require_env("LAB_DATABASE__URL"))
    owner_template = make_url(_require_env("LAB_DATABASE__MIGRATION_URL"))
    graph_template = make_url(_require_env("LAB_DATABASE__GRAPH_URL"))
    created: list[str] = []

    def _make() -> ScratchDatabase:
        name = f"lab_test_{secrets.token_hex(4)}"
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
            conn.execute(f'GRANT CREATE ON DATABASE "{name}" TO lab_owner')
            conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO lab_app')
            conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO lab_graph')
        created.append(name)
        return ScratchDatabase(
            name=name,
            app_url=app_template.set(database=name).render_as_string(hide_password=False),
            owner_url=owner_template.set(database=name).render_as_string(hide_password=False),
            graph_url=graph_template.set(database=name).render_as_string(hide_password=False),
            admin_url=admin_template.set(database=name).render_as_string(hide_password=False),
        )

    yield _make

    with psycopg.connect(admin_url, autocommit=True) as conn:
        for name in created:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def migrated_db(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    db = make_database()
    upgrade("head", url=db.owner_url, app_role="lab_app")
    return db


def settings_for(db: ScratchDatabase) -> Settings:
    return Settings(
        env="test",
        database=DatabaseSettings(
            url=db.app_url, migration_url=db.owner_url, graph_url=db.graph_url
        ),
    )


@pytest_asyncio.fixture
async def app_engine(migrated_db: ScratchDatabase) -> AsyncIterator[AsyncEngine]:
    """Async engine connected as the least-privilege application role."""
    engine = create_engine(settings_for(migrated_db).database)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(app_engine)


@pytest.fixture
def fresh_db(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    """A private, migrated database for tests that count rows or modify shared state."""
    db = make_database()
    upgrade("head", url=db.owner_url, app_role="lab_app")
    return db


PASSWORD = "correct horse battery staple"


def make_user(
    db: ScratchDatabase, username: str, role: str = "viewer", password: str = PASSWORD
) -> None:
    """Create a user through the real service (hashing, validation, audit event)."""
    import asyncio

    from src.core.auth.service import create_user
    from src.core.events.store import Actor, EventContext
    from src.core.security import Role

    async def _run() -> None:
        engine = create_engine(settings_for(db).database)
        try:
            async with create_session_factory(engine)() as session:
                await create_user(
                    session,
                    username=username,
                    password=password,
                    role=Role(role),
                    context=EventContext(Actor.system("test-suite")),
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def sql(url: str, statement: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    """Run one statement synchronously (owner or admin URL) and return its rows, if any."""
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
