"""Phase 2 step 2b.2: run lifecycle service (criteria 2.4 and 2.5, service part).

Real PostgreSQL, real Procrastinate job rows, real LISTEN/NOTIFY. No graph runs here: the runner
side (2b.3, 2b.4) is simulated by calling `transition()` the way a runner will.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.databases.database import create_engine, create_session_factory, session_scope
from src.core.databases.models.runs import ResearchRun
from src.core.errors import ConflictError, KillSwitchEngaged, NotFound
from src.core.events.store import Actor, EventContext, verify_chain
from src.runs import service
from src.runs.dispatch import (
    EXECUTE_RUN_TASK,
    ProcrastinateDispatcher,
    make_job_app,
    run_queueing_lock,
    thread_lock,
)
from src.runs.states import TERMINAL, TRANSITIONS, can_transition
from test.conftest import ScratchDatabase, settings_for, sql

pytestmark = pytest.mark.integration

USER = EventContext(Actor("user", "tester"))
RUNNER = EventContext(Actor.system("runner"))


def plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def lab(fresh_db: ScratchDatabase) -> AsyncIterator[Lab]:
    settings = settings_for(fresh_db)
    engine = create_engine(settings.database)
    try:
        yield Lab(
            fresh_db,
            create_session_factory(engine),
            ProcrastinateDispatcher(make_job_app(settings.database)),
        )
    finally:
        await engine.dispose()


class Lab:
    def __init__(
        self,
        db: ScratchDatabase,
        factory: async_sessionmaker[AsyncSession],
        dispatcher: ProcrastinateDispatcher,
    ) -> None:
        self.db, self.factory, self.dispatcher = db, factory, dispatcher

    async def start(self, **params: Any) -> ResearchRun:
        async with session_scope(self.factory) as s:
            return await service.start_run(
                s,
                graph_key="test",
                graph_version="1",
                params=params,
                context=USER,
                dispatcher=self.dispatcher,
            )

    async def status(self, run_id: uuid.UUID) -> tuple[str, bool]:
        async with self.factory() as s:
            run = await s.get(ResearchRun, run_id)
            assert run is not None
            return run.status, run.pause_requested

    async def runner_moves(self, run_id: uuid.UUID, to: str) -> None:
        async with session_scope(self.factory) as s:
            run = await service.lock_run(s, run_id)
            await service.transition(s, run, to, context=RUNNER)

    def jobs(self) -> list[tuple[str, ...]]:
        return [
            tuple(str(v) for v in row)
            for row in sql(
                self.db.admin_url,
                "SELECT task_name, lock, queueing_lock, status::text "
                "FROM jobs.procrastinate_jobs ORDER BY id",
            )
        ]

    def finish_jobs(self) -> None:
        # Procrastinate's triggers use unqualified names: any direct SQL needs search_path=jobs.
        with psycopg.connect(plain(self.db.admin_url), options="-c search_path=jobs") as conn:
            conn.execute("UPDATE procrastinate_jobs SET status = 'succeeded'")

    def event_types(self) -> list[str]:
        return [
            str(t) for (t,) in sql(self.db.admin_url, "SELECT type FROM lab.events ORDER BY seq")
        ]


class Listener:
    """A LISTEN connection on the control channel, like a runner's."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self.conn = conn

    async def next(self, wait_s: float = 2.0) -> dict[str, Any] | None:
        try:
            async with asyncio.timeout(wait_s):
                async for note in self.conn.notifies():
                    return dict(json.loads(note.payload))
        except TimeoutError:
            return None
        return None


@pytest_asyncio.fixture
async def control(fresh_db: ScratchDatabase) -> AsyncIterator[Listener]:
    async with await psycopg.AsyncConnection.connect(
        plain(fresh_db.app_url), autocommit=True
    ) as conn:
        await conn.execute(f"LISTEN {service.CONTROL_CHANNEL}")
        yield Listener(conn)


# --- status machine ------------------------------------------------------------------------


def test_terminal_statuses_have_no_way_out() -> None:
    assert all(not TRANSITIONS[s] for s in TERMINAL)


def test_every_transition_is_explicit() -> None:
    statuses = list(TRANSITIONS)
    allowed = {(a, b) for a, b in itertools.product(statuses, statuses) if can_transition(a, b)}
    assert ("completed", "running") not in allowed and ("stopped", "queued") not in allowed
    assert ("running", "running") not in allowed  # no silent self-transitions
    assert all(b in TRANSITIONS for _, b in allowed)


async def test_an_illegal_transition_is_refused(lab: Lab) -> None:
    run = await lab.start()
    with pytest.raises(ConflictError):
        await lab.runner_moves(run.id, "completed")  # queued → completed skips running
    assert (await lab.status(run.id))[0] == "queued"


# --- start and dispatch -------------------------------------------------------------------


async def test_start_queues_a_run_with_its_job_and_audit_event(lab: Lab) -> None:
    run = await lab.start(topic="x")
    assert (await lab.status(run.id)) == ("queued", False)
    assert run.thread_id == f"run-{run.id}"
    assert lab.jobs() == [
        (EXECUTE_RUN_TASK, thread_lock(run.thread_id), run_queueing_lock(run.id), "todo")
    ]
    assert lab.event_types() == ["runs.run.created"]


async def test_rollback_leaves_neither_run_nor_job(lab: Lab) -> None:
    async with lab.factory() as s:
        await service.start_run(
            s,
            graph_key="test",
            graph_version="1",
            params={},
            context=USER,
            dispatcher=lab.dispatcher,
        )
        await s.rollback()
    assert sql(lab.db.admin_url, "SELECT count(*) FROM lab.research_runs") == [(0,)]
    assert lab.jobs() == [] and lab.event_types() == []


async def test_enqueue_leaves_the_session_search_path_unchanged(lab: Lab) -> None:
    async with session_scope(lab.factory) as s:
        before = (await s.execute(text("SHOW search_path"))).scalar()
        await service.start_run(
            s,
            graph_key="test",
            graph_version="1",
            params={},
            context=USER,
            dispatcher=lab.dispatcher,
        )
        assert (await s.execute(text("SHOW search_path"))).scalar() == before


async def test_a_second_waiting_execution_is_not_queued(lab: Lab) -> None:
    """Queueing lock: one waiting job per run; the duplicate does not abort the transaction."""
    run = await lab.start()
    async with session_scope(lab.factory) as s:
        queued = await lab.dispatcher.enqueue_run(s, run_id=run.id, thread_id=run.thread_id)
        assert queued is False
        assert (await service.get_kill_switch(s)).engaged is False  # transaction still usable
    assert len(lab.jobs()) == 1


# --- pause and resume ---------------------------------------------------------------------


async def test_pause_of_a_queued_run_is_immediate(lab: Lab, control: Listener) -> None:
    run = await lab.start()
    async with session_scope(lab.factory) as s:
        await service.request_pause(s, run.id, context=USER)
    assert await lab.status(run.id) == ("paused", False)
    assert await control.next(wait_s=0.5) is None  # no runner involved


async def test_pause_of_a_running_run_signals_its_runner(lab: Lab, control: Listener) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    async with session_scope(lab.factory) as s:
        await service.request_pause(s, run.id, context=USER)
    assert await lab.status(run.id) == ("running", True)  # running until the runner drains it
    assert await control.next() == {"action": "pause", "run_id": str(run.id)}

    await lab.runner_moves(run.id, "paused")  # what the runner records after the drain
    assert await lab.status(run.id) == ("paused", False)


async def test_a_rolled_back_pause_sends_nothing(lab: Lab, control: Listener) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    async with lab.factory() as s:
        await service.request_pause(s, run.id, context=USER)
        await s.rollback()
    assert await control.next(wait_s=0.5) is None
    assert await lab.status(run.id) == ("running", False)


async def test_pause_is_idempotent(lab: Lab, control: Listener) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    for _ in range(2):
        async with session_scope(lab.factory) as s:
            await service.request_pause(s, run.id, context=USER)
    assert lab.event_types().count("runs.run.pause_requested") == 1
    assert await control.next() is not None and await control.next(wait_s=0.3) is None


async def test_resume_requeues_a_paused_run(lab: Lab) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    await lab.runner_moves(run.id, "paused")
    lab.finish_jobs()  # the first execution ended
    async with session_scope(lab.factory) as s:
        await service.resume(s, run.id, context=USER, dispatcher=lab.dispatcher)
    assert (await lab.status(run.id))[0] == "queued"
    assert [j[3] for j in lab.jobs()] == ["succeeded", "todo"]


async def test_only_paused_runs_can_be_resumed(lab: Lab) -> None:
    run = await lab.start()
    async with session_scope(lab.factory) as s:
        with pytest.raises(ConflictError):
            await service.resume(s, run.id, context=USER, dispatcher=lab.dispatcher)


# --- stop -----------------------------------------------------------------------------------


@pytest.mark.parametrize("idle", ["queued", "paused", "awaiting_approval"])
async def test_stop_of_an_idle_run_is_immediate(lab: Lab, control: Listener, idle: str) -> None:
    run = await lab.start()
    for step in {
        "queued": [],
        "paused": ["paused"],
        "awaiting_approval": ["running", "awaiting_approval"],
    }[idle]:
        await lab.runner_moves(run.id, step)
    async with session_scope(lab.factory) as s:
        await service.request_stop(s, run.id, context=USER, reason="not needed")
    assert (await lab.status(run.id))[0] == "stopped"
    assert await control.next(wait_s=0.5) is None


async def test_stop_of_a_running_run_is_requested_until_the_runner_confirms(
    lab: Lab, control: Listener
) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    async with session_scope(lab.factory) as s:
        await service.request_stop(s, run.id, context=USER)
    assert (await lab.status(run.id))[0] == "cancel_requested"
    assert await control.next() == {"action": "stop", "run_id": str(run.id)}
    await lab.runner_moves(run.id, "stopped")
    assert (await lab.status(run.id))[0] == "stopped"


async def test_stop_is_idempotent_but_refused_for_finished_runs(lab: Lab) -> None:
    stopped, done = await lab.start(), await lab.start()
    for _ in range(2):
        async with session_scope(lab.factory) as s:
            await service.request_stop(s, stopped.id, context=USER)
    await lab.runner_moves(done.id, "running")
    await lab.runner_moves(done.id, "completed")
    async with session_scope(lab.factory) as s:
        with pytest.raises(ConflictError):
            await service.request_stop(s, done.id, context=USER)
    assert lab.event_types().count("runs.run.stop_requested") == 1


async def test_unknown_run_is_not_found(lab: Lab) -> None:
    async with session_scope(lab.factory) as s:
        with pytest.raises(NotFound):
            await service.request_stop(s, uuid.uuid4(), context=USER)


# --- kill switch ----------------------------------------------------------------------------


async def test_kill_switch_stops_everything_and_blocks_new_work(
    lab: Lab, control: Listener
) -> None:
    queued, paused, running, done = [await lab.start() for _ in range(4)]
    await lab.runner_moves(paused.id, "paused")
    await lab.runner_moves(running.id, "running")
    await lab.runner_moves(done.id, "running")
    await lab.runner_moves(done.id, "completed")

    async with session_scope(lab.factory) as s:
        counts = await service.engage_kill_switch(s, reason="test", context=USER)
    assert counts == (2, 1)
    assert [(await lab.status(r.id))[0] for r in (queued, paused, running, done)] == [
        "stopped",
        "stopped",
        "cancel_requested",
        "completed",
    ]
    assert await control.next() == {"action": "kill", "run_id": None}

    with pytest.raises(KillSwitchEngaged):
        await lab.start()
    async with session_scope(lab.factory) as s:
        assert (await service.get_kill_switch(s)).engaged  # stored: holds after any restart


async def test_kill_switch_blocks_resume_and_release_allows_new_runs(lab: Lab) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "paused")
    sql(lab.db.admin_url, "UPDATE lab.system_state SET kill_switch = true")  # engaged earlier
    async with session_scope(lab.factory) as s:
        with pytest.raises(KillSwitchEngaged):
            await service.resume(s, run.id, context=USER, dispatcher=lab.dispatcher)
    async with session_scope(lab.factory) as s:
        await service.release_kill_switch(s, reason="done", context=USER)
    assert (await lab.start()).status == "queued"


async def test_a_run_cannot_start_while_the_kill_switch_is_being_engaged(lab: Lab) -> None:
    """The start waits for the engaging transaction, then is refused: no run slips through."""
    engaging = lab.factory()
    await service.engage_kill_switch(engaging, reason="race", context=USER)  # not committed yet
    start = asyncio.create_task(lab.start())
    await asyncio.sleep(0.3)
    assert not start.done()  # blocked on the kill-switch row lock
    await engaging.commit()
    await engaging.close()
    with pytest.raises(KillSwitchEngaged):
        await start


async def test_the_audit_chain_stays_valid(lab: Lab) -> None:
    run = await lab.start()
    await lab.runner_moves(run.id, "running")
    async with session_scope(lab.factory) as s:
        await service.request_stop(s, run.id, context=USER)
        await service.engage_kill_switch(s, reason="end", context=USER)
    async with lab.factory() as s:
        result = await verify_chain(s)
    assert result.ok, result
