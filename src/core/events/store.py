"""Event store: append inside the caller's transaction, read, and verify the hash chain.

Appends are serialised with a transaction-scoped advisory lock, so chain order equals commit
order and ``seq`` values become visible to readers in strictly increasing order (a reader that
has seen seq=N will never later discover a committed seq<N). This is what makes
``Last-Event-ID`` resumption on the SSE stream gap-free.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.events import Event
from src.core.events import registry
from src.core.events.hashing import GENESIS_HASH, canonical_bytes, chain_hash
from src.core.events.registry import EventPayload
from src.core.ids import uuid7

# Arbitrary but fixed 64-bit key identifying the event-chain advisory lock.
EVENT_CHAIN_LOCK_KEY = 0x4C41_4245_5654_0001  # "LABEVT" + 1
NOTIFY_CHANNEL = "lab_events"

ActorType = Literal["user", "agent", "system"]


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: str | None = None

    @classmethod
    def system(cls, component: str) -> Actor:
        return cls("system", component)


@dataclass(frozen=True, slots=True)
class EventContext:
    """Who caused the event and which larger process it belongs to."""

    actor: Actor
    correlation_id: str | None = None
    causation_id: uuid.UUID | None = None


async def append(
    session: AsyncSession,
    *,
    type_: str,
    payload: EventPayload | dict[str, Any],
    context: EventContext,
    version: int = 1,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    """Append one event in the session's current transaction.

    The event becomes durable only if the caller commits; on rollback it disappears together
    with the state change it describes. Keep the surrounding transaction short: the chain lock
    is held until commit.
    """
    event_type = registry.get(type_, version)
    body = registry.validate_payload(event_type, payload)

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": EVENT_CHAIN_LOCK_KEY})
    prev = await session.scalar(select(Event.hash).order_by(Event.seq.desc()).limit(1))
    prev_hash = prev if prev is not None else GENESIS_HASH

    now = utcnow()
    event = Event(
        id=uuid7(),
        type=type_,
        type_version=version,
        occurred_at=occurred_at or now,
        recorded_at=now,
        actor_type=context.actor.type,
        actor_id=context.actor.id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=context.correlation_id,
        causation_id=context.causation_id,
        payload=body,
        prev_hash=prev_hash,
    )
    event.hash = chain_hash(prev_hash, _canonical(event))
    session.add(event)
    await session.flush()  # assigns seq
    # Delivered on commit only (NOTIFY is transactional); it is a wake-up hint, never data.
    await session.execute(
        text("SELECT pg_notify(:ch, :seq)"), {"ch": NOTIFY_CHANNEL, "seq": str(event.seq)}
    )
    return event


def _canonical(event: Event) -> bytes:
    return canonical_bytes(
        event_id=event.id,
        type_=event.type,
        type_version=event.type_version,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        payload=event.payload,
    )


async def read_after(
    session: AsyncSession,
    after_seq: int = 0,
    *,
    limit: int = 500,
    types: Sequence[str] | None = None,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
) -> list[Event]:
    stmt = select(Event).where(Event.seq > after_seq).order_by(Event.seq).limit(limit)
    if types:
        stmt = stmt.where(Event.type.in_(types))
    if aggregate_type is not None:
        stmt = stmt.where(Event.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        stmt = stmt.where(Event.aggregate_id == aggregate_id)
    return list((await session.scalars(stmt)).all())


async def read_latest(session: AsyncSession, *, limit: int = 50) -> list[Event]:
    stmt = select(Event).order_by(Event.seq.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def stats(session: AsyncSession) -> tuple[int, int | None]:
    """(total events, last seq)."""
    row = (await session.execute(select(func.count(), func.max(Event.seq)))).one()
    return int(row[0]), row[1]


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None


async def _iter_all(session: AsyncSession, batch: int) -> AsyncIterator[Event]:
    last = 0
    while True:
        rows = await read_after(session, last, limit=batch)
        if not rows:
            return
        for row in rows:
            yield row
        last = rows[-1].seq
        session.expunge_all()


async def verify_chain(session: AsyncSession, *, batch: int = 1000) -> ChainVerification:
    """Recompute every hash from stored data and check every link."""
    expected_prev = GENESIS_HASH
    checked = 0
    async for event in _iter_all(session, batch):
        if event.prev_hash != expected_prev:
            return ChainVerification(
                False, checked, event.seq, "prev_hash does not link to predecessor"
            )
        if chain_hash(event.prev_hash, _canonical(event)) != event.hash:
            return ChainVerification(
                False, checked, event.seq, "content does not match stored hash"
            )
        expected_prev = event.hash
        checked += 1
    return ChainVerification(True, checked)
