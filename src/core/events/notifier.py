"""Process-wide LISTEN connection that wakes up subscribers when new events are committed.

Notifications are only hints: subscribers always re-read the table from their last seen seq, and
also wake up on a timeout, so a lost notification (or a dropped listener connection) delays
delivery but never loses events.
"""

from __future__ import annotations

import asyncio
import contextlib

import psycopg

from src.core.events.store import NOTIFY_CHANNEL
from src.core.logging import get_logger

log = get_logger(__name__)


class EventNotifier:
    def __init__(self, conninfo: str, *, reconnect_delay_s: float = 2.0) -> None:
        self._conninfo = conninfo
        self._reconnect_delay_s = reconnect_delay_s
        self._generation = 0
        self._cond = asyncio.Condition()
        self._task: asyncio.Task[None] | None = None
        self.connected = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-notifier")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def wait(self, max_wait_s: float) -> None:
        """Return when a new event notification arrives or after `max_wait_s` seconds."""
        async with self._cond:
            start = self._generation
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._cond.wait_for(lambda: self._generation != start), max_wait_s
                )

    async def _wake(self) -> None:
        async with self._cond:
            self._generation += 1
            self._cond.notify_all()

    async def _run(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    self.connected.set()
                    log.info("event_notifier.listening", channel=NOTIFY_CHANNEL)
                    await self._wake()  # catch up anything committed while disconnected
                    async for _ in conn.notifies():
                        await self._wake()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on any connection failure
                self.connected.clear()
                log.warning("event_notifier.disconnected", error=str(exc))
                await asyncio.sleep(self._reconnect_delay_s)
