"""Ordered, resumable delivery of audit events (the engine behind the SSE endpoint).

Guarantees:
- events are delivered in strictly increasing ``seq`` order, each exactly once per stream;
- a stream resumed with ``after_seq`` (the SSE ``Last-Event-ID``) continues without gaps or
  duplicates, because appends are serialised and readers always query the table;
- delivery is prompt when notifications work and still happens (after ``max_wait_s``) when they
  do not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.databases.models.events import Event
from src.core.events import store
from src.core.events.notifier import EventNotifier
from src.core.events.schemas import EventOut


async def stream_events(
    session_factory: async_sessionmaker[AsyncSession],
    notifier: EventNotifier,
    *,
    after_seq: int = 0,
    types: Sequence[str] | None = None,
    max_wait_s: float = 15.0,
    batch: int = 500,
) -> AsyncIterator[EventOut | None]:
    """Yield events after `after_seq`, forever. Yields None when idle for `max_wait_s`.

    The None items let the HTTP layer send keep-alive comments so proxies keep the connection.
    """
    last = after_seq
    while True:
        generation = notifier.generation  # read BEFORE querying: see EventNotifier docstring
        async with session_factory() as session:
            rows: list[Event] = await store.read_after(session, last, limit=batch, types=types)
            out = [EventOut.from_row(row) for row in rows]
        for item in out:
            yield item
            last = item.seq
        if out:
            continue  # re-read immediately until drained
        if not await notifier.wait_for_change(generation, max_wait_s):
            yield None  # idle: keep-alive opportunity


AUDIT_EVENT = "audit"  # one SSE event name for every audit event; the type is in the data


def format_sse(item: EventOut | None) -> str:
    """Render one item as a Server-Sent Events frame (`id` = seq, used as Last-Event-ID).

    Browsers' EventSource only delivers event names they subscribed to in advance, so all audit
    events share one name and carry their own `type` in the JSON payload.
    """
    if item is None:
        return ": keep-alive\n\n"
    return f"id: {item.seq}\nevent: {AUDIT_EVENT}\ndata: {item.model_dump_json()}\n\n"
