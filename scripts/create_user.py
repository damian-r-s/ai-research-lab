"""Create a dashboard user (bootstraps the first admin).

Usage:
    python -m scripts.create_user --username alice --role admin          # prompts for password
    printf '%s' "$PW" | python -m scripts.create_user --username alice --role admin --password-stdin

In k3s: kubectl -n ai-lab exec -it deploy/api -- python -m scripts.create_user ... (as in the
sibling project).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from src.core.auth.service import create_user
from src.core.config import get_settings
from src.core.databases.database import create_engine, create_session_factory
from src.core.errors import ConflictError
from src.core.events.store import Actor, EventContext
from src.core.security import Role, WeakPassword


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.read().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("error: passwords do not match")
    return first


async def _create(username: str, password: str, role: Role) -> None:
    engine = create_engine(get_settings().database)
    try:
        async with create_session_factory(engine)() as session:
            user = await create_user(
                session,
                username=username,
                password=password,
                role=role,
                context=EventContext(Actor.system("cli.create_user")),
            )
            await session.commit()
            print(f"created user {user.username!r} with role {user.role}")
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.create_user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    parser.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin"
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_create(args.username, _read_password(args.password_stdin), Role(args.role)))
    except (WeakPassword, ConflictError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
