"""Phase 2 step 2b.1: dispatch and checkpoint schemas, the orchestration tables and their grants
(DECISIONS C-6, C-7; ARCHITECTURE §7)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
from collections.abc import Callable

import psycopg
import pytest

from scripts import migrate as migrate_script
from src.agents.checkpointer import open_checkpointer, setup_checkpointer
from src.core.config import get_settings
from src.core.databases.migrate import MIGRATIONS_DIR, upgrade
from test.conftest import ScratchDatabase, settings_for, sql

pytestmark = pytest.mark.integration


def plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="module")
async def orchestration_db(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    """Migrated database including the checkpointer tables (what `scripts.migrate` produces)."""
    db = make_database()
    upgrade("head", url=db.owner_url, app_role="lab_app", graph_role="lab_graph")
    await setup_checkpointer(settings_for(db).database, readers=["lab_owner"])
    return db


def test_procrastinate_schema_is_pinned() -> None:
    """Upgrading Procrastinate needs a migration that applies its migration files."""
    path = MIGRATIONS_DIR / "versions" / "0002_jobs_and_langgraph.py"
    spec = importlib.util.spec_from_file_location("migration_0002", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert importlib.metadata.version("procrastinate") == module.PROCRASTINATE_SCHEMA_VERSION


def test_job_tables_live_in_jobs_and_app_role_can_enqueue(
    orchestration_db: ScratchDatabase,
) -> None:
    tables = {
        r[0]
        for r in sql(
            orchestration_db.admin_url,
            "SELECT tablename FROM pg_tables WHERE tablename LIKE 'procrastinate%%'",
        )
    }
    in_jobs = {
        r[0]
        for r in sql(
            orchestration_db.admin_url,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'jobs'",
        )
    }
    assert tables == in_jobs >= {"procrastinate_jobs", "procrastinate_workers"}
    with psycopg.connect(plain(orchestration_db.app_url), options="-c search_path=jobs") as conn:
        row = conn.execute(
            "SELECT unnest(procrastinate_defer_jobs_v2(ARRAY[ROW('q', 't', 0, NULL, NULL, "
            "'{}'::jsonb, NULL, 'ordered')]::procrastinate_job_to_defer_v2[]))"
        ).fetchone()
        assert row is not None and row[0] > 0
        conn.rollback()


def test_checkpointer_tables_belong_to_the_graph_role(orchestration_db: ScratchDatabase) -> None:
    rows = sql(
        orchestration_db.admin_url,
        "SELECT schemaname, tableowner FROM pg_tables WHERE tablename LIKE 'checkpoint%%'",
    )
    assert rows and set(rows) == {("langgraph", "lab_graph")}


async def test_checkpointer_setup_is_idempotent(orchestration_db: ScratchDatabase) -> None:
    await setup_checkpointer(settings_for(orchestration_db).database)
    async with open_checkpointer(settings_for(orchestration_db).database) as saver:
        assert await saver.aget_tuple({"configurable": {"thread_id": "none"}}) is None


def test_graph_role_is_confined_and_app_role_cannot_read_checkpoints(
    orchestration_db: ScratchDatabase,
) -> None:
    checks = sql(
        orchestration_db.admin_url,
        "SELECT has_schema_privilege('lab_graph', 'lab', 'USAGE'),"
        "       has_schema_privilege('lab_graph', 'jobs', 'USAGE'),"
        "       has_schema_privilege('lab_graph', 'public', 'CREATE'),"
        "       has_database_privilege('lab_graph', current_database(), 'CREATE'),"
        "       has_schema_privilege('lab_app', 'langgraph', 'USAGE')",
    )[0]
    assert checks == (False, False, False, False, False)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM lab.research_runs",
        "DELETE FROM lab.agent_tasks",
        "UPDATE lab.budget_ledger SET tokens = 0",
        "DELETE FROM lab.budget_ledger",
        "DELETE FROM lab.system_state",
        "INSERT INTO lab.system_state (id, kill_switch, changed_at) VALUES (2, false, now())",
    ],
)
def test_app_role_cannot_erase_the_record(
    orchestration_db: ScratchDatabase, statement: str
) -> None:
    with (
        psycopg.connect(plain(orchestration_db.app_url)) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(statement)


def test_kill_switch_row_exists_and_is_off(orchestration_db: ScratchDatabase) -> None:
    assert sql(orchestration_db.owner_url, "SELECT id, kill_switch FROM lab.system_state") == [
        (1, False)
    ]
    with (
        psycopg.connect(plain(orchestration_db.admin_url)) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            "INSERT INTO lab.system_state (id, kill_switch, changed_at) VALUES (2, true, now())"
        )


def test_scripts_migrate_also_sets_up_the_checkpointer(
    make_database: Callable[[], ScratchDatabase], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_database()
    monkeypatch.setenv("LAB_DATABASE__URL", db.app_url)
    monkeypatch.setenv("LAB_DATABASE__MIGRATION_URL", db.owner_url)
    monkeypatch.setenv("LAB_DATABASE__GRAPH_URL", db.graph_url)
    get_settings.cache_clear()
    try:
        assert migrate_script.main(["upgrade"]) == 0
    finally:
        get_settings.cache_clear()
    assert sql(
        db.admin_url,
        "SELECT count(*) FROM pg_tables "
        "WHERE schemaname = 'langgraph' AND tablename = 'checkpoints'",
    ) == [(1,)]


PG_DUMP = shutil.which("pg_dump")


@pytest.mark.skipif(PG_DUMP is None, reason="pg_dump not installed")
@pytest.mark.parametrize("scope", [["--schema-only"], ["--data-only", "--schema=langgraph"]])
def test_backup_role_can_dump_every_schema(
    orchestration_db: ScratchDatabase, scope: list[str]
) -> None:
    """The nightly backup runs pg_dump as the schema owner; it must be able to read checkpoints."""
    assert PG_DUMP is not None
    result = subprocess.run(  # noqa: S603 - fixed tool, throwaway test database
        [PG_DUMP, *scope, "--dbname", plain(orchestration_db.owner_url)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_scripts_migrate_requires_the_graph_role_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = get_settings()
    prod = base.model_copy(
        update={"env": "prod", "database": base.database.model_copy(update={"graph_url": None})}
    )
    monkeypatch.setattr(migrate_script, "get_settings", lambda: prod)
    with pytest.raises(SystemExit, match="graph_url is required"):
        migrate_script.setup_checkpointer_if_configured()
