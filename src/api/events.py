"""Audit log: paged listing and a live Server-Sent Events stream (any logged-in role)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import DbSession, State, require_role, session_token
from src.core.auth.service import Principal, authenticate
from src.core.errors import LabError, NotAuthenticated
from src.core.events import store
from src.core.events.schemas import EventOut
from src.core.events.stream import format_sse, stream_events
from src.core.security import Role

router = APIRouter(prefix="/api/events", tags=["events"])
Viewer = Annotated[Principal, Depends(require_role(Role.viewer))]

_RETRY_MS = 3000  # how long browsers wait before reconnecting a dropped stream


class InvalidLastEventId(LabError):
    code = "invalid_last_event_id"


class EventPage(BaseModel):
    events: list[EventOut]
    next_after: int  # pass as `after` to get the following page


@router.get("", response_model=EventPage)
async def list_events(
    _: Viewer,
    session: DbSession,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    type: Annotated[list[str] | None, Query()] = None,
) -> EventPage:
    rows = await store.read_after(session, after, limit=limit, types=type)
    events = [EventOut.from_row(r) for r in rows]
    return EventPage(events=events, next_after=events[-1].seq if events else after)


@router.get("/latest", response_model=list[EventOut])
async def latest_events(
    _: Viewer, session: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[EventOut]:
    """Newest first: the dashboard's initial view before it switches to the stream."""
    return [EventOut.from_row(r) for r in await store.read_latest(session, limit=limit)]


def _start_seq(last_event_id: str | None, after: int | None) -> int:
    if last_event_id is not None:  # browser reconnect: the header wins
        if not last_event_id.isdigit():
            raise InvalidLastEventId("Last-Event-ID must be a non-negative integer")
        return int(last_event_id)
    return after or 0


@router.get("/stream", response_class=StreamingResponse)
async def stream(
    request: Request,
    _: Viewer,
    state: State,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    type: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    """Live audit events as SSE. `id:` is the event seq; reconnects resume via Last-Event-ID.

    The session is re-checked every keep-alive interval: logging out (or expiry) ends the stream.
    """
    start = _start_seq(last_event_id, after)
    token = session_token(request, state)
    interval = state.settings.api.sse_keepalive_s

    async def still_authenticated() -> bool:
        try:
            async with state.session_factory() as session:
                await authenticate(session, token)
                await session.commit()
        except NotAuthenticated:
            return False
        return True

    async def frames() -> AsyncIterator[str]:
        yield f"retry: {_RETRY_MS}\n\n"
        last_check = time.monotonic()
        async for item in stream_events(
            state.session_factory, state.notifier, after_seq=start, types=type, max_wait_s=interval
        ):
            if time.monotonic() - last_check >= interval:
                if not await still_authenticated():
                    yield "event: session_ended\ndata: {}\n\n"
                    return
                last_check = time.monotonic()
            yield format_sse(item)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
