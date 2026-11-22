"""Backend for browser end-to-end tests: real API on a throwaway, migrated database.

Usage (started by Playwright, see frontend/playwright.config.ts):
    E2E_PASSWORD=... python -m test.e2e_server --port 8790

Creates users `e2e-admin` (admin) and `e2e-viewer` (viewer), serves until terminated, then drops
the database. Never touches the development database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

import psycopg
import uvicorn
from sqlalchemy.engine import make_url

from src.api.app import create_app
from src.core.auth.service import create_user
from src.core.config import (
    ApiSettings,
    AuthSettings,
    DatabaseSettings,
    LoggingSettings,
    Settings,
)
from src.core.databases.database import create_engine, create_session_factory
from src.core.databases.migrate import upgrade
from src.core.events.store import Actor, EventContext
from src.core.security import Role


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set (source scripts/env.sh)")
    return value


async def _seed(settings: Settings, password: str) -> None:
    engine = create_engine(settings.database)
    try:
        async with create_session_factory(engine)() as session:
            for username, role in (("e2e-admin", Role.admin), ("e2e-viewer", Role.viewer)):
                await create_user(
                    session,
                    username=username,
                    password=password,
                    role=role,
                    context=EventContext(Actor.system("e2e-setup")),
                )
            await session.commit()
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    password = _env("E2E_PASSWORD")
    admin_url = _env("LAB_TEST_ADMIN_URL")
    name = f"lab_e2e_{secrets.token_hex(4)}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        # E2E runs are serial: anything left by an interrupted earlier run is garbage.
        stale = conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'lab_e2e_%%'")
        for (old,) in stale.fetchall():
            conn.execute(f'DROP DATABASE IF EXISTS "{old}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
        conn.execute(f'GRANT CREATE ON DATABASE "{name}" TO lab_owner')
        conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO lab_app')
    try:
        app_url = make_url(_env("LAB_DATABASE__URL")).set(database=name)
        owner_url = make_url(_env("LAB_DATABASE__MIGRATION_URL")).set(database=name)
        settings = Settings(
            env="test",
            database=DatabaseSettings(
                url=app_url.render_as_string(hide_password=False),
                migration_url=owner_url.render_as_string(hide_password=False),
            ),
            auth=AuthSettings(cookie_secure=False),
            logging=LoggingSettings(level="WARNING", format="console"),
            # Like production: the API sits behind nginx and trusts only that proxy's header.
            api=ApiSettings(trusted_proxies=["127.0.0.1/32"]),
        )
        migration_url = settings.database.effective_migration_url().get_secret_value()
        upgrade("head", app_role="lab_app", url=migration_url)
        asyncio.run(_seed(settings, password))
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
