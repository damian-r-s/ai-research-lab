"""Process lifecycle events: restarts are visible in the audit trail."""

from __future__ import annotations

import os
import socket

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import VERSION
from src.core.events import store
from src.core.events.registry import EventPayload, register
from src.core.events.store import Actor, EventContext
from src.core.logging import get_logger

log = get_logger(__name__)


class ProcessLifecycle(EventPayload):
    component: str
    version: str
    hostname: str
    pid: int


PROCESS_STARTED = register(
    "system.process.started", 1, ProcessLifecycle, "A lab process started and is serving"
)
PROCESS_STOPPED = register(
    "system.process.stopped", 1, ProcessLifecycle, "A lab process shut down gracefully"
)


async def record_lifecycle(
    session_factory: async_sessionmaker[AsyncSession], event_type: str, component: str
) -> bool:
    """Best effort: a database outage must not stop the process from starting or stopping.

    Returns False (and logs an error) if the event could not be written.
    """
    payload = ProcessLifecycle(
        component=component, version=VERSION, hostname=socket.gethostname(), pid=os.getpid()
    )
    try:
        async with session_factory() as session:
            await store.append(
                session,
                type_=event_type,
                payload=payload,
                context=EventContext(Actor.system(component)),
                aggregate_type="process",
                aggregate_id=f"{payload.hostname}:{payload.pid}",
            )
            await session.commit()
    except Exception as exc:
        log.error("lifecycle_event.not_recorded", event_type=event_type, error=str(exc))
        return False
    return True
