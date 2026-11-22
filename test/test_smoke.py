"""Phase 1 step 1 smoke tests: the skeleton starts, migrations apply, health endpoints work."""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from test.conftest import ScratchDatabase, settings_for

pytestmark = pytest.mark.integration


def _psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def test_live_is_ok_without_touching_dependencies(migrated_db: ScratchDatabase) -> None:
    with TestClient(create_app(settings_for(migrated_db))) as client:
        response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_migrated(migrated_db: ScratchDatabase) -> None:
    with TestClient(create_app(settings_for(migrated_db))) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database_reachable": True,
        "migrations_current": True,
    }


def test_not_ready_when_migrations_missing(make_database: Callable[[], ScratchDatabase]) -> None:
    empty = make_database()  # exists and is reachable, but no migrations applied
    with TestClient(create_app(settings_for(empty))) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["database_reachable"] is True
    assert response.json()["migrations_current"] is False


def test_migrations_create_foundation_tables_with_least_privilege(
    migrated_db: ScratchDatabase,
) -> None:
    with psycopg.connect(_psycopg_url(migrated_db.owner_url)) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'lab'"
            ).fetchall()
        }
        event_privileges = {
            r[0]
            for r in conn.execute(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'lab' AND table_name = 'events' AND grantee = 'lab_app'"
            ).fetchall()
        }
    assert {"users", "auth_sessions", "login_attempts", "events", "alembic_version"} <= tables
    # The audit log is append-only for the application role.
    assert event_privileges == {"SELECT", "INSERT"}
