"""A-7: Procrastinate (DECISIONS D-03) as the dispatch layer.

Transactional enqueue with our SQLAlchemy session, one runner per thread (lock), recovery of a
job whose worker was `kill -9`ed, retries, periodic jobs with several workers, and abort.
"""

from __future__ import annotations

import asyncio
import itertools
import subprocess
import time
from collections import defaultdict
from datetime import datetime

import pytest
from sqlalchemy import text

from src.core.config import DatabaseSettings
from src.core.databases.database import create_engine, create_session_factory
from test.spike.conftest import Processes
from test.spike.jobs_app import make_app
from test.spike.support import SpikeDB, query

pytestmark = [pytest.mark.integration, pytest.mark.spike]


def start_worker(procs: Processes, name: str, **env: str) -> subprocess.Popen[str]:
    proc = procs.start("test.spike.jobs_app", name, env=env)
    assert proc.stdout is not None and proc.stdout.readline() == "worker ready\n"
    return proc


async def wait_rows(
    db: SpikeDB, statement: str, params: tuple[object, ...] = (), limit_s: float = 20
) -> None:
    deadline = time.monotonic() + limit_s
    while not query(db.owner_url, statement, params)[0][0]:
        if time.monotonic() > deadline:
            raise TimeoutError(statement)
        await asyncio.sleep(0.05)


def runs(db: SpikeDB, task: str) -> list[tuple[str, str, str, datetime]]:
    return [
        (t, w, e, at)
        for t, w, e, at in query(
            db.owner_url,
            "SELECT tag, worker, event, at FROM spike.job_runs WHERE task = %s ORDER BY id",
            (task,),
        )
    ]


def job_status(db: SpikeDB) -> dict[str, str]:
    return {
        args["tag"]: status
        for args, status in query(
            db.owner_url, "SELECT args, status::text FROM jobs.procrastinate_jobs"
        )
    }


async def test_a7_enqueue_joins_our_sqlalchemy_transaction(spike_db: SpikeDB) -> None:
    db = spike_db
    engine = create_engine(
        DatabaseSettings(url=db.domain_url.replace("postgresql://", "postgresql+psycopg://", 1))
    )
    factory = create_session_factory(engine)
    app = make_app(db.domain_url)
    try:
        for tag, commit in (("rolled-back", False), ("committed", True)):
            async with factory() as session:
                await session.execute(
                    text("INSERT INTO spike.tasks (key, status) VALUES (:k, 'queued')"), {"k": tag}
                )
                await session.execute(text("SET LOCAL search_path = jobs"))
                raw = (await (await session.connection()).get_raw_connection()).driver_connection
                await app.configure_task("hold", connection=raw).defer_async(tag=tag)
                await (session.commit() if commit else session.rollback())
    finally:
        await engine.dispose()
    assert query(db.owner_url, "SELECT key FROM spike.tasks") == [("committed",)]
    assert job_status(db) == {"committed": "todo"}  # the rolled-back job never existed


async def test_a7_lock_gives_one_runner_per_thread_across_workers(
    spike_db: SpikeDB, procs: Processes
) -> None:
    db = spike_db
    # One job at a time per worker: any overlap between T and U proves two workers ran at once.
    start_worker(procs, "w1", SPIKE_CONCURRENCY="1")
    start_worker(procs, "w2", SPIKE_CONCURRENCY="1")
    async with make_app(db.domain_url).open_async() as app:
        for i in range(3):
            for thread in ("T", "U"):
                await app.configure_task("hold", lock=f"thread:{thread}").defer_async(
                    tag=f"{thread}{i}"
                )
    await wait_rows(db, "SELECT count(*) = 6 FROM spike.job_runs WHERE event = 'end'")

    spans: dict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
    start: dict[str, datetime] = {}
    for tag, worker, event, at in runs(db, "hold"):
        if event == "start":
            start[tag] = at
        else:
            spans[tag[0]].append((start[tag], at, worker))
    for thread, intervals in spans.items():
        intervals.sort()
        for (_, end, _), (next_start, _, _) in itertools.pairwise(intervals):
            assert end <= next_start, (thread, intervals)  # never two at once for one thread
    t, u = spans["T"], spans["U"]
    parallel = [(wa, wb) for a0, a1, wa in t for b0, b1, wb in u if a0 < b1 and b0 < a1]
    assert parallel  # different threads do run at the same time ...
    assert all(wa != wb for wa, wb in parallel)  # ... on different workers


async def test_a7_job_of_a_killed_worker_is_recovered_and_its_lock_holds_meanwhile(
    spike_db: SpikeDB, procs: Processes
) -> None:
    db = spike_db
    doomed = start_worker(procs, "doomed", SPIKE_HOLD_SECONDS="3600")
    async with make_app(db.domain_url).open_async() as app:
        for tag in ("K1", "K2"):
            await app.configure_task("hold", lock="thread:K").defer_async(tag=tag)
        await wait_rows(db, "SELECT count(*) FROM spike.job_runs WHERE tag = 'K1'")
        procs.kill9(doomed)
        start_worker(procs, "rescuer", SPIKE_HOLD_SECONDS="0.2")
        await asyncio.sleep(1.5)
        # The dead worker's job still holds the thread lock: K2 must not start on its own.
        assert [r for r in runs(db, "hold") if r[0] == "K2"] == []
        assert job_status(db) == {"K1": "doing", "K2": "todo"}

        # Reaper (a periodic job in 2b): stalled = worker heartbeat older than the timeout.
        await asyncio.sleep(2.5)
        stalled = list(await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=2))
        assert [j.task_kwargs["tag"] for j in stalled] == ["K1"]
        for job in stalled:
            await app.job_manager.retry_job(job)
    await wait_rows(db, "SELECT count(*) = 2 FROM spike.job_runs WHERE event = 'end'")
    history = [(t, w, e) for t, w, e, _ in runs(db, "hold")]
    assert history == [
        ("K1", "doomed", "start"),
        ("K1", "rescuer", "start"),
        ("K1", "rescuer", "end"),
        ("K2", "rescuer", "start"),
        ("K2", "rescuer", "end"),
    ]


async def test_a7_transient_errors_are_retried_and_permanent_ones_are_not(
    spike_db: SpikeDB, procs: Processes
) -> None:
    db = spike_db
    start_worker(procs, "w1")
    async with make_app(db.domain_url).open_async() as app:
        await app.configure_task("flaky").defer_async(kind="transient", tag="t")
        await app.configure_task("flaky").defer_async(kind="permanent", tag="p")
        await app.configure_task("flaky").defer_async(kind="always_transient", tag="x")
    await wait_rows(
        db,
        "SELECT count(*) = 3 FROM jobs.procrastinate_jobs WHERE status IN ('succeeded', 'failed')",
    )
    events = defaultdict(list)
    for tag, _, event, _ in runs(db, "flaky"):
        events[tag].append(event)
    assert events["t"] == ["attempt:0", "attempt:1", "attempt:2", "succeeded"]
    assert events["p"] == ["attempt:0"]  # not in retry_exceptions: never retried
    # max_attempts=3 counts *retries*: the first run plus 3 retries, then the job fails.
    assert events["x"] == ["attempt:0", "attempt:1", "attempt:2", "attempt:3"]
    assert job_status(db) == {"t": "succeeded", "p": "failed", "x": "failed"}


async def test_a7_periodic_job_runs_once_per_tick_with_two_workers(
    spike_db: SpikeDB, procs: Processes
) -> None:
    db = spike_db
    start_worker(procs, "w1", SPIKE_PERIODIC="1")
    start_worker(procs, "w2", SPIKE_PERIODIC="1")
    await wait_rows(
        db, "SELECT count(DISTINCT tag) >= 3 FROM spike.job_runs WHERE task = 'tick'", limit_s=15
    )
    per_tick = query(
        db.owner_url, "SELECT tag, count(*) FROM spike.job_runs WHERE task = 'tick' GROUP BY tag"
    )
    assert all(n == 1 for _, n in per_tick), per_tick


async def test_a7_abort_cancels_a_running_job_promptly_and_cancel_drops_a_queued_one(
    spike_db: SpikeDB, procs: Processes
) -> None:
    db = spike_db
    start_worker(procs, "w1")
    async with make_app(db.domain_url).open_async() as app:
        running = await app.configure_task("sleeper").defer_async(tag="running")
        queued = await app.configure_task("hold", schedule_in={"hours": 1}).defer_async(
            tag="queued"
        )
        await wait_rows(db, "SELECT count(*) FROM spike.job_runs WHERE tag = 'running'")
        started = time.monotonic()
        await app.job_manager.cancel_job_by_id_async(running, abort=True)
        await wait_rows(
            db, "SELECT count(*) FROM spike.job_runs WHERE tag = 'running' AND event = 'cancelled'"
        )
        latency = time.monotonic() - started
        assert await app.job_manager.cancel_job_by_id_async(queued)
    assert latency < 2.0, latency
    await wait_rows(db, "SELECT count(*) FROM jobs.procrastinate_jobs WHERE status = 'aborted'")
    assert job_status(db) == {"running": "aborted", "queued": "cancelled"}
