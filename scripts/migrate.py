"""Apply or roll back database migrations.

Usage:
    python -m scripts.migrate upgrade [revision]      # default: head
    python -m scripts.migrate downgrade <revision>
    python -m scripts.migrate current

Runs as the schema-owner role (database.migration_url). In k3s this is the api pod's
`migrate` init container, as in the sibling project.
"""

from __future__ import annotations

import sys

from alembic import command

from src.core.databases.migrate import alembic_config, downgrade, upgrade

USAGE = "usage: python -m scripts.migrate upgrade [revision] | downgrade <revision> | current"


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    action, args = argv[0], argv[1:]
    if action == "upgrade":
        upgrade(args[0] if args else "head")
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
