"""Programmatic Alembic entry points (used by `python -m scripts.migrate`, readiness and tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str | None = None, *, app_role: str | None = None) -> Config:
    """Alembic config without an alembic.ini. `url` defaults to database.migration_url."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if app_role:
        cfg.attributes["app_role"] = app_role
    return cfg


def expected_heads() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def upgrade(revision: str = "head", *, url: str | None = None, app_role: str | None = None) -> None:
    command.upgrade(alembic_config(url, app_role=app_role), revision)


def downgrade(revision: str, *, url: str | None = None, app_role: str | None = None) -> None:
    command.downgrade(alembic_config(url, app_role=app_role), revision)
