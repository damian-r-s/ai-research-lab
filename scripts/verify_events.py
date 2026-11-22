"""Verify the audit log's hash chain end to end.

Usage: python -m scripts.verify_events

Exit code 0 = chain intact, 1 = tampering or corruption detected (first bad seq is reported).
Only needs read access, so it runs with the application role (database.url).
"""

from __future__ import annotations

import asyncio
import sys

from src.core.config import get_settings
from src.core.databases.database import create_engine, create_session_factory
from src.core.events.store import verify_chain


async def _verify() -> int:
    engine = create_engine(get_settings().database)
    try:
        async with create_session_factory(engine)() as session:
            result = await verify_chain(session)
    finally:
        await engine.dispose()
    if result.ok:
        print(f"OK: hash chain intact ({result.checked} events verified)")
        return 0
    print(
        f"FAILED: chain broken at seq {result.first_bad_seq} ({result.reason}); "
        f"{result.checked} events before it verified",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_verify()))
