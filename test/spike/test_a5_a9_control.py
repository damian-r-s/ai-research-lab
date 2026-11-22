"""A-5: hard stop by cancelling the asyncio task that runs `ainvoke`.
A-9 (added during the spike): graceful pause via LangGraph's `RunControl.request_drain()`.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl

from test.spike.graphs import Ctx, build, config, initial, open_pool, open_saver
from test.spike.support import SpikeDB, count, query

pytestmark = [pytest.mark.integration, pytest.mark.spike]

STOP_TARGET_S = 10.0  # ARCHITECTURE §6.6


async def wait_for_effect(
    db: SpikeDB, thread: str, node: str, event: str, limit_s: float = 10
) -> None:
    deadline = time.monotonic() + limit_s
    while count(db, thread, node, event) == 0:
        if time.monotonic() > deadline:
            raise TimeoutError((node, event))
        await asyncio.sleep(0.02)


async def test_a5_cancel_stops_a_running_async_node_promptly_and_resume_works(
    checkpointer_ready: SpikeDB,
) -> None:
    db, thread = checkpointer_ready, "a5"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("linear").compile(checkpointer=saver)
        ctx = Ctx(pool=pool, dsn=db.domain_url, hang_at=frozenset({"b"}))  # b awaits "I/O" for 1 h
        run = asyncio.create_task(
            graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        )
        await wait_for_effect(db, thread, "b", "hanging")
        started = time.monotonic()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        latency = time.monotonic() - started
        assert latency < 1.0 < STOP_TARGET_S, latency

        snap = await graph.aget_state(config(thread))
        assert snap.next == ("b",)  # consistent: last checkpoint is after a, b pending
        assert snap.values["steps"] == ["a"]

        await graph.ainvoke(
            None, config(thread), context=Ctx(pool=pool, dsn=db.domain_url), durability="sync"
        )
        final = await graph.aget_state(config(thread))
    assert final.values["steps"] == ["a", "b", "agent-answer", "d"]
    assert count(db, thread, "a", "start") == 1 and count(db, thread, "b", "start") == 2


async def test_a5_cancel_does_not_stop_a_synchronous_node_thread(
    checkpointer_ready: SpikeDB,
) -> None:
    """Finding: a sync node runs in a worker thread. Cancelling `ainvoke` returns at once, but
    the thread keeps running (and writing) until it finishes. Rule for 2b: nodes and tools must
    be async; blocking work goes behind an async interface with its own cancellation."""
    db, thread = checkpointer_ready, "a5-sync"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("sync").compile(checkpointer=saver)
        ctx = Ctx(pool=pool, dsn=db.domain_url, extra={"ticks": 15})  # ~1.5 s of blocking work
        run = asyncio.create_task(
            graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        )
        await wait_for_effect(db, thread, "sync_worker", "tick1")
        started = time.monotonic()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert time.monotonic() - started < 1.0  # the *caller* is released promptly ...
        ticks_at_cancel = sum(1 for n, _ in _effects(db, thread) if n == "sync_worker")
        await asyncio.sleep(2.0)
    ticks_later = sum(1 for n, _ in _effects(db, thread) if n == "sync_worker")
    assert ticks_later > ticks_at_cancel  # ... but the node's work went on after the "stop"


def _effects(db: SpikeDB, thread: str) -> list[tuple[str, str]]:
    return [
        (n, e)
        for n, e in query(
            db.owner_url, "SELECT node, event FROM spike.effects WHERE thread = %s", (thread,)
        )
    ]


async def test_a9_drain_pauses_at_the_next_node_boundary_and_resumes(
    checkpointer_ready: SpikeDB,
) -> None:
    db, thread = checkpointer_ready, "a9"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("linear").compile(checkpointer=saver)
        control = RunControl()
        ctx = Ctx(pool=pool, dsn=db.domain_url, delay=0.5)
        run = asyncio.create_task(
            graph.ainvoke(
                initial(thread),
                config(thread),
                context=ctx,
                durability="sync",
                control=control,
            )
        )
        await wait_for_effect(db, thread, "b", "start")
        control.request_drain("pause")
        with pytest.raises(GraphDrained):
            await run
        snap = await graph.aget_state(config(thread))
        assert snap.values["steps"] == ["a", "b"]  # b finished cleanly; nothing was cut off
        assert snap.next == ("agent",)
        assert count(db, thread, "agent", "start") == 0

        await graph.ainvoke(
            None, config(thread), context=Ctx(pool=pool, dsn=db.domain_url), durability="sync"
        )
        final = await graph.aget_state(config(thread))
    assert final.values["steps"] == ["a", "b", "agent-answer", "d"]
    assert all(count(db, thread, n, "start") == 1 for n in ("a", "b", "agent", "d"))
