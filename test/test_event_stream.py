"""Criterion 1.13 (stream engine): ordered, prompt, resumable delivery without gaps or duplicates.

The HTTP/SSE wiring is tested in step 4, once the endpoint can be protected by login.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.databases.database import create_engine, create_session_factory, libpq_url
from src.core.databases.migrate import upgrade
from src.core.events import store
from src.core.events.notifier import EventNotifier
from src.core.events.schemas import EventOut
from src.core.events.stream import format_sse, stream_events
from test.conftest import ScratchDatabase, settings_for
from test.test_events import CTX, THING_HAPPENED, ThingHappened

pytestmark = pytest.mark.integration


@pytest.fixture
def db(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    database = make_database()
    upgrade("head", url=database.owner_url, app_role="lab_app")
    return database


@pytest_asyncio.fixture
async def factory(db: ScratchDatabase) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_for(db).database)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def notifier(db: ScratchDatabase) -> AsyncIterator[EventNotifier]:
    n = EventNotifier(libpq_url(db.app_url))
    await n.start()
    await asyncio.wait_for(n.connected.wait(), 10)
    yield n
    await n.stop()


async def _append(factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with factory() as session:
        event = await store.append(
            session, type_=THING_HAPPENED.name, payload=ThingHappened(name=name), context=CTX
        )
        await session.commit()
        return event.seq


class Collector:
    """Consumes a stream in the background, like a connected browser would."""

    def __init__(self, stream: AsyncIterator[EventOut | None]) -> None:
        self.events: list[EventOut] = []
        self.keepalives = 0
        self._stream = stream
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async for item in self._stream:
            if item is None:
                self.keepalives += 1
            else:
                self.events.append(item)

    async def wait_for(self, count: int, limit_s: float = 5.0) -> float:
        """Wait until `count` events arrived; return how long it took."""
        start = time.monotonic()
        while len(self.events) < count:
            if time.monotonic() - start > limit_s:
                raise AssertionError(f"got {len(self.events)} of {count} events in {limit_s}s")
            await asyncio.sleep(0.01)
        return time.monotonic() - start

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


async def test_live_events_arrive_in_order_and_promptly(
    factory: async_sessionmaker[AsyncSession], notifier: EventNotifier
) -> None:
    # A long idle wait proves delivery is driven by notifications, not by polling.
    collector = Collector(stream_events(factory, notifier, max_wait_s=30))
    try:
        await asyncio.sleep(0.1)  # subscriber is connected and idle
        seqs = [await _append(factory, f"live{i}") for i in range(5)]
        elapsed = await collector.wait_for(5, limit_s=5)
        assert [e.seq for e in collector.events] == seqs
        assert [e.payload["name"] for e in collector.events] == [f"live{i}" for i in range(5)]
        assert elapsed < 2.0
    finally:
        await collector.close()


async def test_resume_after_last_event_id_has_no_gaps_or_duplicates(
    factory: async_sessionmaker[AsyncSession], notifier: EventNotifier
) -> None:
    seqs = [await _append(factory, f"e{i}") for i in range(6)]

    first = Collector(stream_events(factory, notifier, max_wait_s=30))
    await first.wait_for(3)
    last_seen = first.events[2].seq  # client disconnects after the 3rd event
    await first.close()

    more = [await _append(factory, f"later{i}") for i in range(3)]  # while disconnected

    resumed = Collector(stream_events(factory, notifier, after_seq=last_seen, max_wait_s=30))
    try:
        await resumed.wait_for(6)
        received = [e.seq for e in resumed.events]
        assert received == seqs[3:] + more  # exactly the missed ones, in order
        assert len(received) == len(set(received))
    finally:
        await resumed.close()


async def test_delivery_still_happens_without_notifications(
    factory: async_sessionmaker[AsyncSession], db: ScratchDatabase
) -> None:
    """If the LISTEN connection is down, polling after max_wait_s still delivers everything."""
    silent = EventNotifier(libpq_url(db.app_url))  # never started: no notifications at all
    collector = Collector(stream_events(factory, silent, max_wait_s=0.3))
    try:
        await asyncio.sleep(0.1)
        await _append(factory, "polled")
        await collector.wait_for(1, limit_s=3)
        assert collector.events[0].payload["name"] == "polled"
        assert collector.keepalives >= 1  # idle periods produce keep-alives
    finally:
        await collector.close()


async def test_notifier_reconnects_after_its_connection_is_killed(
    factory: async_sessionmaker[AsyncSession], db: ScratchDatabase
) -> None:
    """Simulates a database restart or network blip on the LISTEN connection."""
    notifier = EventNotifier(libpq_url(db.app_url), reconnect_delay_s=0.1)
    await notifier.start()
    await asyncio.wait_for(notifier.connected.wait(), 10)
    collector = Collector(stream_events(factory, notifier, max_wait_s=30))
    try:
        with psycopg.connect(
            db.admin_url.replace("postgresql+psycopg://", "postgresql://")
        ) as conn:
            killed = conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND query LIKE 'LISTEN%%'"
            ).fetchall()
        assert killed == [(True,)]  # exactly the notifier's connection was terminated
        await _append(factory, "after-blip")
        # Delivered via the reconnect catch-up wake-up, long before the 30 s poll fallback.
        await collector.wait_for(1, limit_s=5)
        assert collector.events[0].payload["name"] == "after-blip"
        await asyncio.wait_for(notifier.connected.wait(), 5)
    finally:
        await collector.close()
        await notifier.stop()


async def test_large_backlog_is_drained_completely(
    factory: async_sessionmaker[AsyncSession], notifier: EventNotifier
) -> None:
    seqs = [await _append(factory, f"b{i}") for i in range(25)]
    collector = Collector(stream_events(factory, notifier, batch=7, max_wait_s=30))
    try:
        await collector.wait_for(25)
        assert [e.seq for e in collector.events] == seqs
    finally:
        await collector.close()


async def test_type_filter(
    factory: async_sessionmaker[AsyncSession], notifier: EventNotifier
) -> None:
    await _append(factory, "x")
    collector = Collector(
        stream_events(factory, notifier, types=["auth.login.failed"], max_wait_s=0.2)
    )
    try:
        await asyncio.sleep(0.6)
        assert collector.events == []
    finally:
        await collector.close()


def test_sse_frame_format() -> None:
    assert format_sse(None) == ": keep-alive\n\n"
    item = EventOut.model_validate(
        {
            "seq": 7,
            "id": "0192f0c4-0000-7000-8000-000000000000",
            "type": "test.thing.happened",
            "type_version": 1,
            "occurred_at": "2026-11-22T10:00:00Z",
            "recorded_at": "2026-11-22T10:00:00Z",
            "actor_type": "system",
            "actor_id": None,
            "aggregate_type": None,
            "aggregate_id": None,
            "correlation_id": None,
            "causation_id": None,
            "payload": {"name": "x"},
            "hash": "ab" * 32,
        }
    )
    frame = format_sse(item)
    assert frame.startswith("id: 7\nevent: test.thing.happened\ndata: {")
    assert frame.endswith("}\n\n")
    assert "\n\n" not in frame[:-2]  # one frame, no embedded blank lines
