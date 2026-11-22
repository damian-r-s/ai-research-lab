"""Criterion 1.2: migrations are reversible and the ORM models match the migrated schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, MetaData, Text, create_engine, pool

from src.core.databases.base import SCHEMA
from src.core.databases.migrate import downgrade, expected_heads, upgrade
from src.core.databases.models import metadata
from test.conftest import ScratchDatabase

pytestmark = pytest.mark.integration

FOUNDATION_TABLES = {"users", "auth_sessions", "login_attempts", "events"}


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _lab_tables(db: ScratchDatabase) -> set[str]:
    with psycopg.connect(_plain(db.owner_url)) as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (SCHEMA,))
        return {r[0] for r in rows} - {"alembic_version"}


def _event_triggers(db: ScratchDatabase) -> set[str]:
    with psycopg.connect(_plain(db.owner_url)) as conn:
        rows = conn.execute(
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = 'events' AND NOT t.tgisinternal",
            (SCHEMA,),
        )
        return {r[0] for r in rows}


def _include_name(name: str | None, type_: Any, _parents: Any) -> bool:
    return name == SCHEMA if type_ == "schema" else True


def test_upgrade_downgrade_upgrade_round_trip(
    make_database: Callable[[], ScratchDatabase],
) -> None:
    db = make_database()

    upgrade("head", url=db.owner_url, app_role="lab_app")
    assert _lab_tables(db) == FOUNDATION_TABLES
    assert _event_triggers(db) == {"events_append_only", "events_no_truncate"}

    downgrade("base", url=db.owner_url, app_role="lab_app")
    assert _lab_tables(db) == set()

    upgrade("head", url=db.owner_url, app_role="lab_app")
    assert _lab_tables(db) == FOUNDATION_TABLES
    assert _event_triggers(db) == {"events_append_only", "events_no_truncate"}


def test_single_migration_head() -> None:
    """Branching migration histories must be merged explicitly, never left with two heads."""
    assert len(expected_heads()) == 1


def _schema_diff(db: ScratchDatabase, target: MetaData) -> list[Any]:
    engine = create_engine(db.owner_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_schemas": True,
                    "include_name": _include_name,
                    "version_table_schema": SCHEMA,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            return list(compare_metadata(context, target))
    finally:
        engine.dispose()


def test_orm_models_match_migrated_schema(migrated_db: ScratchDatabase) -> None:
    """Autogenerate against the migrated DB must find nothing: no drift between code and SQL."""
    assert _schema_diff(migrated_db, metadata) == []


def test_drift_check_detects_a_model_change(migrated_db: ScratchDatabase) -> None:
    """Guard against a vacuous check: a column missing from the migrations must be reported."""
    drifted = MetaData(schema=SCHEMA, naming_convention=metadata.naming_convention)
    for table in metadata.sorted_tables:
        table.to_metadata(drifted)
    drifted.tables[f"{SCHEMA}.users"].append_column(Column("nickname", Text))
    diff = _schema_diff(migrated_db, drifted)
    assert any(d[0] == "add_column" and d[3].name == "nickname" for d in diff)


def test_app_role_cannot_run_ddl(migrated_db: ScratchDatabase) -> None:
    """The runtime role has DML only; schema changes go through migrations as the owner."""
    with psycopg.connect(_plain(migrated_db.app_url)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"CREATE TABLE {SCHEMA}.not_allowed (id int)")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DROP TABLE {SCHEMA}.users")
