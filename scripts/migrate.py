"""Apply or roll back database migrations.

Usage:
    python -m scripts.migrate upgrade [revision]      # default: head
    python -m scripts.migrate downgrade <revision>
    python -m scripts.migrate current

Runs as the schema-owner role (database.migration_url). In k3s this is the api pod's
`migrate` init container, as in the sibling project.

`upgrade` then sets up the LangGraph checkpointer's tables in schema `langgraph`, as the graph role
(database.graph_url), so every schema is created in one place (DECISIONS C-7). It is required in
staging and production; elsewhere it is skipped with a notice when graph_url is unset.
"""

from __future__ import annotations

import asyncio
import sys

from alembic import command

from src.core.config import get_settings
from src.core.databases.migrate import alembic_config, downgrade, upgrade

USAGE = "usage: python -m scripts.migrate upgrade [revision] | downgrade <revision> | current"


def setup_checkpointer_if_configured() -> None:
    settings = get_settings()
    if settings.database.graph_url is None:
        if settings.env in {"staging", "prod"}:
            raise SystemExit("database.graph_url is required in staging and prod")
        print("notice: database.graph_url not set; checkpointer setup skipped", file=sys.stderr)
        return
    from sqlalchemy.engine import make_url

    from src.agents.checkpointer import setup_checkpointer

    # The migration role also takes the backups (k8s/base/backup.yaml), so it must read checkpoints.
    owner = make_url(settings.database.effective_migration_url().get_secret_value()).username
    asyncio.run(setup_checkpointer(settings.database, readers=[owner] if owner else []))


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    action, args = argv[0], argv[1:]
    if action == "upgrade":
        upgrade(args[0] if args else "head")
        if not args or args[0] == "head":
            setup_checkpointer_if_configured()
    elif action == "downgrade" and len(args) == 1:
        downgrade(args[0])
    elif action == "current":
        command.current(alembic_config(), verbose=True)
    else:
        print(USAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
