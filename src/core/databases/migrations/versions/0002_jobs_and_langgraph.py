"""Schemas `jobs` (Procrastinate) and `langgraph` (LangGraph checkpointer), DECISIONS C-6/C-7.

- `jobs`: Procrastinate's own schema file for the pinned version, applied with
  `search_path = jobs` because the library's SQL is not schema-qualified. Upgrading Procrastinate
  requires a new migration that applies its migration files; `test_procrastinate_schema_is_pinned`
  fails until that exists.
- `langgraph`: created empty here. The checkpointer creates its own tables in it as the graph role
  (`scripts.migrate` runs `setup()` after Alembic), so that role needs USAGE and CREATE on this
  schema only, and nothing on `lab` or `jobs`.

Revision ID: 0002_jobs_and_langgraph
Revises: 0001_foundation
Create Date: 2026-11-22
"""

from collections.abc import Sequence
from importlib.resources import files

from alembic import op

revision: str = "0002_jobs_and_langgraph"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROCRASTINATE_SCHEMA_VERSION = "3.12.0"  # the installed version whose schema.sql is applied


def _grant_if_role(role: str, statements: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            {statements}
          ELSE
            RAISE WARNING 'role % does not exist; no grants issued', '{role}';
          END IF;
        END $$;
        """
    )


def upgrade(app_role: str, graph_role: str, **_: str) -> None:
    op.execute("CREATE SCHEMA jobs")
    op.execute("SET LOCAL search_path = jobs")
    schema_sql = (files("procrastinate.sql") / "schema.sql").read_text(encoding="utf-8")
    # Raw DBAPI cursor without parameters (same connection and transaction): the file contains
    # `%` and `:` characters that SQLAlchemy or psycopg would otherwise read as placeholders.
    dbapi = op.get_bind().connection.dbapi_connection
    assert dbapi is not None
    dbapi.cursor().execute(schema_sql)
    op.execute("RESET search_path")
    _grant_if_role(
        app_role,
        f"""
        GRANT USAGE ON SCHEMA jobs TO "{app_role}";
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA jobs TO "{app_role}";
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA jobs TO "{app_role}";
        """,
    )

    op.execute("CREATE SCHEMA langgraph")
    _grant_if_role(graph_role, f'GRANT USAGE, CREATE ON SCHEMA langgraph TO "{graph_role}";')


def downgrade(app_role: str, graph_role: str, **_: str) -> None:
    op.execute("DROP SCHEMA langgraph CASCADE")  # includes the checkpointer's own tables
    op.execute("DROP SCHEMA jobs CASCADE")
