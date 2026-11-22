"""Alembic environment. Runs as the schema-owner role (database.migration_url)."""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool, text

from src.core.config import get_settings
from src.core.databases.base import SCHEMA
from src.core.databases.models import metadata

config = context.config


def _url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    return explicit or get_settings().database.effective_migration_url().get_secret_value()


def _app_role() -> str:
    return config.attributes.get("app_role") or get_settings().database.app_role


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations(app_role=_app_role())


def _include_name(name: str | None, type_: Any, parent_names: Any) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    return True


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        conn.commit()
        context.configure(
            connection=conn,
            target_metadata=metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=_include_name,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations(app_role=_app_role())
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
