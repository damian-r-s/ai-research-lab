"""Database layout for the spike, mirroring ARCHITECTURE §7.1 (one PostgreSQL, three schemas).

- `langgraph`: owned by its own role `lab_graph`, which can touch nothing else (A-6).
- `jobs`: Procrastinate's tables, owned by `lab_owner`, DML for the runtime role `lab_app` (A-7).
- `spike`: stand-in for the domain record (`agent_tasks`, attempts, model-call cache, effects).
"""

from __future__ import annotations

import importlib.resources
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

GRAPH_ROLE = "lab_graph"

DOMAIN_SQL = """
CREATE TABLE spike.effects (
    id bigserial PRIMARY KEY, thread text NOT NULL, node text NOT NULL, event text NOT NULL,
    os_pid int NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE spike.tasks (key text PRIMARY KEY, status text NOT NULL, result text);
CREATE TABLE spike.attempts (
    id bigserial PRIMARY KEY, key text NOT NULL REFERENCES spike.tasks, status text NOT NULL,
    os_pid int NOT NULL);
CREATE TABLE spike.model_calls (
    key text NOT NULL, seq int NOT NULL, result text NOT NULL, PRIMARY KEY (key, seq));
CREATE TABLE spike.job_runs (
    id bigserial PRIMARY KEY, task text NOT NULL, tag text NOT NULL, worker text NOT NULL,
    event text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp());
GRANT USAGE ON SCHEMA spike TO lab_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA spike TO lab_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA spike TO lab_app;
"""

JOBS_GRANTS_SQL = """
GRANT USAGE ON SCHEMA jobs TO lab_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA jobs TO lab_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA jobs TO lab_app;
"""


def libpq(url: str, *, user: str | None = None) -> str:
    u = make_url(url).set(drivername="postgresql")
    if user:
        u = u.set(username=user)
    return u.render_as_string(hide_password=False)


@dataclass(frozen=True)
class SpikeDB:
    name: str
    graph_url: str  # role lab_graph; search_path must be set to `langgraph` on connect
    domain_url: str  # role lab_app (runtime): domain tables + job DML
    owner_url: str  # role lab_owner
    admin_url: str  # superuser: test inspection only

    def env(self) -> dict[str, str]:
        return {"SPIKE_GRAPH_URL": self.graph_url, "SPIKE_DOMAIN_URL": self.domain_url}


def prepare(name: str, app_url: str, owner_url: str, admin_url: str) -> SpikeDB:
    admin = libpq(admin_url)
    owner = libpq(owner_url)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lab_graph') "
            "THEN CREATE ROLE lab_graph LOGIN; END IF; END $$"
        )
        conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO {GRAPH_ROLE}')
        conn.execute(f"CREATE SCHEMA langgraph AUTHORIZATION {GRAPH_ROLE}")
        conn.execute("CREATE SCHEMA jobs AUTHORIZATION lab_owner")
        conn.execute("CREATE SCHEMA spike AUTHORIZATION lab_owner")
    schema = (importlib.resources.files("procrastinate.sql") / "schema.sql").read_text()
    with psycopg.connect(owner, autocommit=True, options="-c search_path=jobs") as conn:
        conn.execute(schema)
        conn.execute(JOBS_GRANTS_SQL)
        conn.execute(DOMAIN_SQL)
    return SpikeDB(
        name=name,
        graph_url=libpq(app_url, user=GRAPH_ROLE),
        domain_url=libpq(app_url),
        owner_url=owner,
        admin_url=admin,
    )


def query(url: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else []


def wait_until(url: str, statement: str, params: tuple[Any, ...] = (), timeout: float = 20) -> None:
    """Poll until the query returns a truthy first column (used to hit exact crash points)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = query(url, statement, params)
        if rows and rows[0][0]:
            return
        time.sleep(0.05)
    raise TimeoutError(statement)


def effects(db: SpikeDB, thread: str) -> list[tuple[str, str]]:
    return [
        (n, e)
        for n, e in query(
            db.owner_url,
            "SELECT node, event FROM spike.effects WHERE thread = %s ORDER BY id",
            (thread,),
        )
    ]


def count(db: SpikeDB, thread: str, node: str, event: str) -> int:
    return effects(db, thread).count((node, event))
