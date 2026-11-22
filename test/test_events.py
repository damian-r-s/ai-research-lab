"""Criteria 1.3 to 1.7: the audit log is transactional, append-only, tamper-evident, validated."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, LiteralString

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.config import REPO_ROOT
from src.core.databases.database import create_engine, create_session_factory
from src.core.databases.migrate import upgrade
from src.core.databases.models import Event
from src.core.events import registry, store
from src.core.events.hashing import canonical_bytes
from src.core.events.registry import EventPayload, InvalidEventPayload, UnknownEventType
from src.core.events.store import Actor, EventContext
from test.conftest import ScratchDatabase, settings_for

pytestmark = pytest.mark.integration


class ThingHappened(EventPayload):
    name: str
    amount: float = 0.0
    tags: tuple[str, ...] = ()


THING_HAPPENED = registry.register("test.thing.happened", 1, ThingHappened, "Test-only event type")
CTX = EventContext(Actor.system("test-suite"), correlation_id="corr-1")


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def fresh_db(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    """A private, migrated database: tests here tamper with the chain or count rows."""
    db = make_database()
    upgrade("head", url=db.owner_url, app_role="lab_app")
    return db


@pytest_asyncio.fixture
async def factory(fresh_db: ScratchDatabase) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_engine(settings_for(fresh_db).database)
    yield create_session_factory(engine)
    await engine.dispose()


async def _append(factory: async_sessionmaker[AsyncSession], name: str, **extra: Any) -> Event:
    async with factory() as session:
        event = await store.append(
            session,
            type_=THING_HAPPENED.name,
            payload=ThingHappened(name=name, **extra),
            context=CTX,
            aggregate_type="thing",
            aggregate_id=name,
        )
        await session.commit()
        return event


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(Event)) or 0)


async def _verify(factory: async_sessionmaker[AsyncSession]) -> store.ChainVerification:
    async with factory() as session:
        return await store.verify_chain(session)


# --- 1.3 transactional ----------------------------------------------------------------------


async def test_event_in_rolled_back_transaction_does_not_exist(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _append(factory, "kept")
    async with factory() as session:
        await store.append(
            session, type_=THING_HAPPENED.name, payload={"name": "lost"}, context=CTX
        )
        await session.rollback()
    async with factory() as session:
        names = [e.payload["name"] for e in await store.read_after(session, 0)]
    assert names == ["kept"]
    assert (await _verify(factory)).ok


async def test_stored_event_round_trips_all_fields(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    written = await _append(factory, "café ✓", amount=0.1, tags=("a", "b"))
    async with factory() as session:
        (read,) = await store.read_after(session, 0)
    assert read.id == written.id
    assert read.payload == {"name": "café ✓", "amount": 0.1, "tags": ["a", "b"]}
    assert read.actor_type == "system" and read.actor_id == "test-suite"
    assert read.correlation_id == "corr-1"
    assert read.aggregate_type == "thing" and read.aggregate_id == "café ✓"
    assert read.occurred_at.tzinfo is not None


# --- 1.4 append-only ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE lab.events SET type = 'x'",
        "DELETE FROM lab.events",
        "TRUNCATE lab.events",
    ],
)
async def test_app_role_cannot_modify_events(
    factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase, statement: str
) -> None:
    await _append(factory, "one")
    with (
        psycopg.connect(_plain(fresh_db.app_url)) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE lab.events SET type = 'x'",
        "DELETE FROM lab.events",
        "TRUNCATE lab.events",
    ],
)
async def test_even_the_schema_owner_cannot_modify_events(
    factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase, statement: str
) -> None:
    """The trigger protects the log regardless of grants (owner bypasses grants, not triggers)."""
    await _append(factory, "one")
    with (
        psycopg.connect(_plain(fresh_db.owner_url)) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"),
    ):
        conn.execute(statement)
    assert await _count(factory) == 1


# --- 1.5 tamper evidence --------------------------------------------------------------------


async def test_untouched_chain_verifies(factory: async_sessionmaker[AsyncSession]) -> None:
    for i in range(5):
        await _append(factory, f"e{i}", amount=i / 3)
    result = await _verify(factory)
    assert result.ok and result.checked == 5


def _tamper(db: ScratchDatabase, sql: LiteralString, params: tuple[object, ...]) -> None:
    """Simulate an attacker with superuser rights: disable triggers for this session only."""
    with psycopg.connect(_plain(db.admin_url)) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute(sql, params)
        conn.commit()


async def test_modified_payload_is_detected(
    factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase
) -> None:
    events = [await _append(factory, f"e{i}") for i in range(4)]
    target = events[2].seq
    _tamper(
        fresh_db,
        "UPDATE lab.events SET payload = %s::jsonb WHERE seq = %s",
        ('{"name": "forged"}', target),
    )
    result = await _verify(factory)
    assert not result.ok
    assert result.first_bad_seq == target
    assert result.reason == "content does not match stored hash"
    assert result.checked == 2


async def test_deleted_event_is_detected(
    factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase
) -> None:
    events = [await _append(factory, f"e{i}") for i in range(4)]
    _tamper(fresh_db, "DELETE FROM lab.events WHERE seq = %s", (events[1].seq,))
    result = await _verify(factory)
    assert not result.ok
    assert result.first_bad_seq == events[2].seq  # its prev_hash points at the missing event
    assert result.reason == "prev_hash does not link to predecessor"


async def test_recomputed_hash_of_forged_event_is_still_detected(
    factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase
) -> None:
    """Rewriting an event AND its own hash still breaks the next event's link."""
    events = [await _append(factory, f"e{i}") for i in range(3)]
    _tamper(
        fresh_db, "UPDATE lab.events SET hash = sha256('forged') WHERE seq = %s", (events[0].seq,)
    )
    result = await _verify(factory)
    assert not result.ok
    assert result.first_bad_seq == events[0].seq


def test_verify_script_reports_status(fresh_db: ScratchDatabase) -> None:
    env = {k: v for k, v in os.environ.items() if k != "LAB_DATABASE__URL"}
    env["LAB_DATABASE__URL"] = fresh_db.app_url
    ok = subprocess.run(
        [sys.executable, "-m", "scripts.verify_events"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert "OK: hash chain intact (0 events verified)" in ok.stdout


# --- 1.6 concurrency ------------------------------------------------------------------------


async def test_concurrent_appends_keep_a_consistent_chain(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    n = 60
    await asyncio.gather(*(_append(factory, f"c{i}") for i in range(n)))
    async with factory() as session:
        rows = await store.read_after(session, 0, limit=n + 10)
    seqs = [r.seq for r in rows]
    assert len(rows) == n
    assert seqs == sorted(seqs) and len(set(seqs)) == n
    assert {r.payload["name"] for r in rows} == {f"c{i}" for i in range(n)}
    result = await _verify(factory)
    assert result.ok and result.checked == n


# --- 1.7 validation -------------------------------------------------------------------------


async def test_unknown_event_type_is_rejected(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        with pytest.raises(UnknownEventType):
            await store.append(session, type_="test.unknown.happened", payload={}, context=CTX)


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing required field
        {"name": "x", "unexpected": 1},  # unknown field
        {"name": "x", "amount": "lots"},  # wrong type
    ],
)
async def test_invalid_payload_is_rejected(
    factory: async_sessionmaker[AsyncSession], payload: dict[str, object]
) -> None:
    async with factory() as session:
        with pytest.raises(InvalidEventPayload):
            await store.append(session, type_=THING_HAPPENED.name, payload=payload, context=CTX)
    assert await _count(factory) == 0


async def test_wrong_payload_model_is_rejected(factory: async_sessionmaker[AsyncSession]) -> None:
    class Other(EventPayload):
        name: str

    async with factory() as session:
        with pytest.raises(InvalidEventPayload):
            await store.append(
                session, type_=THING_HAPPENED.name, payload=Other(name="x"), context=CTX
            )


def test_event_type_names_must_follow_convention() -> None:
    for bad in ("nodots", "Upper.case.Name", "has space.x", "trailing.", ".leading"):
        with pytest.raises(ValueError, match=re.escape("domain.entity.verb_past")):
            registry.register(bad, 1, ThingHappened, "bad")


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_bytes(
            event_id=uuid.uuid4(),
            type_="t.x.y",
            type_version=1,
            occurred_at=datetime(2026, 1, 1),  # deliberately naive
            recorded_at=datetime(2026, 1, 1),
            actor_type="system",
            actor_id=None,
            aggregate_type=None,
            aggregate_id=None,
            correlation_id=None,
            causation_id=None,
            payload={},
        )
