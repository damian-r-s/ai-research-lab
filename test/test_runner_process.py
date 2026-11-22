"""Phase 2 step 2b.4: the runner process (criteria 2.2, 2.3, 2.4, 2.6 across processes).

Every runner is a real `python -m src.services.runner` process against a scratch database, with
short heartbeat and reaper intervals. Crashes are `kill -9`; shutdowns are SIGTERM.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents.checkpointer import open_checkpointer, prune_finished_runs, setup_checkpointer
from src.agents.graphs import selftest
from src.core.config import REPO_ROOT, Settings
from src.core.databases.database import create_engine, create_session_factory, session_scope
from src.core.databases.models.runs import AgentTask, AgentTaskAttempt, Approval, ResearchRun
from src.core.errors import KillSwitchEngaged
from src.core.events.store import Actor, EventContext
from src.runs import approvals, service
from src.runs.dispatch import ProcrastinateDispatcher, make_job_app
from test.conftest import ScratchDatabase, settings_for, sql

pytestmark = pytest.mark.integration

USER = EventContext(Actor("user", "tester"))
FAST = {
    "LAB_RUNNER__HEARTBEAT_INTERVAL_S": "0.5",
    "LAB_RUNNER__STALL_TIMEOUT_S": "2",
    "LAB_RUNNER__REAPER_INTERVAL_S": "1",
    "LAB_RUNNER__CONTROL_POLL_INTERVAL_S": "1",
    "LAB_RUNNER__SHUTDOWN_TIMEOUT_S": "15",
}


class Lab:
    def __init__(
        self,
        db: ScratchDatabase,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        logs: Path,
    ) -> None:
        self.db, self.settings, self.factory, self.logs = db, settings, factory, logs
        self.dispatcher = ProcrastinateDispatcher(make_job_app(settings.database))
        self.procs: list[subprocess.Popen[bytes]] = []

    # runners ---------------------------------------------------------------------------------

    def runner(self, name: str) -> subprocess.Popen[bytes]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_DATABASE__")}
        env.update(
            FAST,
            LAB_ENV="test",
            LAB_DATABASE__URL=self.db.app_url,
            LAB_DATABASE__MIGRATION_URL=self.db.owner_url,
            LAB_DATABASE__GRAPH_URL=self.db.graph_url,
        )
        log = (self.logs / f"{name}.log").open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.services.runner"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.procs.append(proc)
        self.wait(lambda: self.runner_status(proc.pid) == "active", f"{name} started")
        return proc

    def runner_status(self, pid: int) -> str | None:
        rows = sql(
            self.db.admin_url,
            "SELECT status FROM lab.runners WHERE pid = %s ORDER BY started_at DESC LIMIT 1",
            (pid,),
        )
        return str(rows[0][0]) if rows else None

    @staticmethod
    def kill9(proc: subprocess.Popen[bytes]) -> None:
        proc.send_signal(signal.SIGKILL)
        assert proc.wait(timeout=10) == -signal.SIGKILL

    @staticmethod
    def terminate(proc: subprocess.Popen[bytes], timeout: float = 30) -> int:
        proc.send_signal(signal.SIGTERM)
        return proc.wait(timeout=timeout)

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)

    # runs ------------------------------------------------------------------------------------

    async def start(self, **params: Any) -> uuid.UUID:
        async with session_scope(self.factory) as s:
            run = await service.start_run(
                s,
                graph_key=selftest.KEY,
                graph_version=selftest.VERSION,
                params=params,
                context=USER,
                dispatcher=self.dispatcher,
            )
            return run.id

    def status(self, run_id: uuid.UUID) -> str:
        return str(
            sql(self.db.admin_url, "SELECT status FROM lab.research_runs WHERE id = %s", (run_id,))[
                0
            ][0]
        )

    def task_status(self, run_id: uuid.UUID, name: str) -> str | None:
        rows = sql(
            self.db.admin_url,
            "SELECT status FROM lab.agent_tasks WHERE run_id = %s AND idempotency_key = %s",
            (run_id, f"run-{run_id}:{name}" if ":" in name else f"run-{run_id}:{name}:"),
        )
        return str(rows[0][0]) if rows else None

    async def attempts(
        self, run_id: uuid.UUID
    ) -> dict[str, list[tuple[str | None, Any, Any, Any]]]:
        async with self.factory() as s:
            rows = (
                await s.execute(
                    select(AgentTask.idempotency_key, AgentTaskAttempt)
                    .join(AgentTaskAttempt, AgentTaskAttempt.agent_task_id == AgentTask.id)
                    .where(AgentTask.run_id == run_id)
                    .order_by(AgentTaskAttempt.attempt_no)
                )
            ).all()
        result: dict[str, list[tuple[str | None, Any, Any, Any]]] = {}
        for key, a in rows:
            result.setdefault(key.split(":", 1)[1].rstrip(":"), []).append(
                (a.outcome, a.runner_id, a.started_at, a.ended_at)
            )
        return result

    def wait_status(self, run_id: uuid.UUID, *statuses: str, limit_s: float = 30) -> float:
        return self.wait(lambda: self.status(run_id) in statuses, f"{run_id} {statuses}", limit_s)

    def wait_task(self, run_id: uuid.UUID, name: str, status: str) -> float:
        return self.wait(lambda: self.task_status(run_id, name) == status, f"{name} {status}")

    @staticmethod
    def wait(check: Callable[[], bool], what: str, limit_s: float = 30) -> float:
        started = time.monotonic()
        while not check():
            if time.monotonic() - started > limit_s:
                raise TimeoutError(what)
            time.sleep(0.05)
        return time.monotonic() - started


@pytest.fixture
def lab_logs(tmp_path: Path) -> Path:
    return tmp_path


@pytest_asyncio.fixture
async def lab(fresh_db: ScratchDatabase, lab_logs: Path) -> AsyncIterator[Lab]:
    settings = settings_for(fresh_db)
    await setup_checkpointer(settings.database)
    engine = create_engine(settings.database)
    instance = Lab(fresh_db, settings, create_session_factory(engine), lab_logs)
    try:
        yield instance
    finally:
        instance.cleanup()
        await engine.dispose()


def outcomes(
    attempts: dict[str, list[tuple[str | None, Any, Any, Any]]],
) -> dict[str, list[str | None]]:
    return {k: [a[0] for a in v] for k, v in attempts.items()}


# --- basic operation --------------------------------------------------------------------------


async def test_a_runner_executes_queued_runs_and_keeps_its_heartbeat(lab: Lab) -> None:
    runs = [await lab.start(steps=2, fanout=2) for _ in range(3)]
    proc = lab.runner("a")
    for run_id in runs:
        lab.wait_status(run_id, "completed")
    first = sql(
        lab.db.admin_url, "SELECT heartbeat_at FROM lab.runners WHERE pid = %s", (proc.pid,)
    )[0][0]
    await asyncio.sleep(1.2)
    later = sql(
        lab.db.admin_url, "SELECT heartbeat_at FROM lab.runners WHERE pid = %s", (proc.pid,)
    )[0][0]
    assert isinstance(first, datetime) and isinstance(later, datetime) and later > first
    assert lab.terminate(proc) == 0
    assert lab.runner_status(proc.pid) == "stopped"


# --- 2.2: kill -9 mid-run -----------------------------------------------------------------------


async def test_kill9_mid_run_resumes_on_another_runner_from_the_checkpoint(lab: Lab) -> None:
    run_id = await lab.start(steps=4, delay_s=1.0)
    first = lab.runner("first")
    lab.wait(lambda: lab.task_status(run_id, "step:2") == "running", "step 2 running")
    lab.kill9(first)

    second = lab.runner("second")
    lab.wait(
        lambda: lab.status(run_id) == "completed", "completed on the second runner", limit_s=45
    )

    result = await lab.attempts(run_id)
    assert outcomes(result)["step:1"] == ["succeeded"]  # finished work is never redone
    assert outcomes(result)["step:2"] == ["abandoned", "succeeded"]  # interrupted attempt kept
    assert all(o[-1] == "succeeded" for o in outcomes(result).values())
    assert sql(
        lab.db.admin_url,
        "SELECT count(*), count(DISTINCT idempotency_key) FROM lab.agent_tasks WHERE run_id = %s",
        (run_id,),
    )[0] == (len(result), len(result))  # one logical task per position
    assert lab.runner_status(first.pid) == "dead"
    reasons = [
        r
        for (r,) in sql(
            lab.db.admin_url,
            "SELECT payload->>'reason' FROM lab.events WHERE type = 'runs.run.status_changed'",
        )
    ]
    assert "runner lost" in reasons
    assert lab.terminate(second) == 0


# --- 2.3: one runner per thread -------------------------------------------------------------------


async def test_two_runners_never_execute_the_same_thread_at_once(lab: Lab) -> None:
    runs = [await lab.start(steps=3, delay_s=0.2) for _ in range(6)]
    a, b = lab.runner("a"), lab.runner("b")
    # Dispatch every run a second time while it is running: the thread lock must serialise them.
    for run_id in runs:
        lab.wait_status(run_id, "running", "completed")
        async with session_scope(lab.factory) as s:
            run = await s.get(ResearchRun, run_id)
            assert run is not None
            await lab.dispatcher.enqueue_run(s, run_id=run.id, thread_id=run.thread_id)
    for run_id in runs:
        lab.wait_status(run_id, "completed")
    lab.wait(
        lambda: (
            sql(
                lab.db.admin_url,
                "SELECT count(*) FROM jobs.procrastinate_jobs "
                "WHERE status IN ('todo', 'doing') AND queue_name = 'runs'",
            )[0][0]
            == 0
        ),
        "duplicate jobs drained",
    )
    runners_used = set()
    for run_id in runs:
        spans = sorted(
            (start, end)
            for attempts in (await lab.attempts(run_id)).values()
            for _, rid, start, end in attempts
        )
        runners_used |= {
            rid for attempts in (await lab.attempts(run_id)).values() for _, rid, _, _ in attempts
        }
        for (_, end), (start, _) in itertools.pairwise(spans):
            assert end <= start  # never two attempts of one run at the same time
        assert all(o == ["succeeded"] for o in outcomes(await lab.attempts(run_id)).values())
    assert len(runners_used) == 2  # both runners did work
    for p in (a, b):
        assert lab.terminate(p) == 0


# --- 2.4: stop and kill switch across processes -----------------------------------------------


async def test_stop_reaches_the_runner_and_ends_the_run_within_10_seconds(lab: Lab) -> None:
    run_id = await lab.start(steps=2, delay_s=60)
    proc = lab.runner("a")
    lab.wait(lambda: lab.task_status(run_id, "plan") == "running", "running")
    async with session_scope(lab.factory) as s:
        await service.request_stop(s, run_id, context=USER, reason="test")
    latency = lab.wait(lambda: lab.status(run_id) == "stopped", "stopped", limit_s=10)
    assert latency < 10.0
    assert outcomes(await lab.attempts(run_id))["plan"] == ["cancelled"]
    assert lab.terminate(proc) == 0


async def test_kill_switch_stops_all_runs_and_holds_across_a_runner_restart(lab: Lab) -> None:
    runs = [await lab.start(steps=2, delay_s=60) for _ in range(2)]
    proc = lab.runner("a")
    for run_id in runs:
        lab.wait_task(run_id, "plan", "running")
    async with session_scope(lab.factory) as s:
        await service.engage_kill_switch(s, reason="test", context=USER)
    for run_id in runs:
        assert lab.wait_status(run_id, "stopped", limit_s=10) < 10
    assert lab.terminate(proc) == 0

    restarted = lab.runner("b")
    with pytest.raises(KillSwitchEngaged):
        await lab.start()
    assert lab.terminate(restarted) == 0


# --- 2.5: pause across processes ----------------------------------------------------------------


async def test_pause_and_resume_across_runner_processes(lab: Lab) -> None:
    run_id = await lab.start(steps=4, delay_s=0.5)
    a = lab.runner("a")
    lab.wait(lambda: lab.task_status(run_id, "step:1") == "running", "step 1 running")
    async with session_scope(lab.factory) as s:
        await service.request_pause(s, run_id, context=USER)
    lab.wait(lambda: lab.status(run_id) == "paused", "paused")
    assert lab.terminate(a) == 0  # nothing holds the run while it is paused

    b = lab.runner("b")
    async with session_scope(lab.factory) as s:
        await service.resume(s, run_id, context=USER, dispatcher=lab.dispatcher)
    lab.wait(lambda: lab.status(run_id) == "completed", "completed")
    assert all(o == ["succeeded"] for o in outcomes(await lab.attempts(run_id)).values())
    assert lab.terminate(b) == 0


# --- graceful shutdown --------------------------------------------------------------------------


async def test_sigterm_drains_the_run_and_another_runner_continues_it(lab: Lab) -> None:
    run_id = await lab.start(steps=4, delay_s=1.0)
    a = lab.runner("a")
    lab.wait(lambda: lab.task_status(run_id, "step:2") == "running", "step 2 running")
    assert lab.terminate(a) == 0
    assert lab.status(run_id) == "queued"  # handed back, not paused and not stopped
    assert outcomes(await lab.attempts(run_id))["step:2"] == ["succeeded"]  # finished first

    b = lab.runner("b")
    lab.wait(lambda: lab.status(run_id) == "completed", "completed")
    assert all(o == ["succeeded"] for o in outcomes(await lab.attempts(run_id)).values())
    assert lab.terminate(b) == 0


# --- 2.6: approval across processes -------------------------------------------------------------


async def test_an_approval_survives_a_runner_restart(lab: Lab) -> None:
    run_id = await lab.start(steps=1, approval=True)
    a = lab.runner("a")
    lab.wait(lambda: lab.status(run_id) == "awaiting_approval", "waiting")
    assert lab.terminate(a) == 0

    b = lab.runner("b")
    async with lab.factory() as s:
        (approval,) = (await s.scalars(select(Approval).where(Approval.run_id == run_id))).all()
    async with session_scope(lab.factory) as s:
        await approvals.decide(
            s, approval.id, approved=True, comment=None, context=USER, dispatcher=lab.dispatcher
        )
    lab.wait(lambda: lab.status(run_id) == "completed", "completed")
    assert lab.terminate(b) == 0


# --- 2.11: checkpoint pruning ----------------------------------------------------------------


async def test_pruning_keeps_the_final_state_and_the_domain_record(lab: Lab) -> None:
    old, recent = await lab.start(steps=3), await lab.start(steps=3)
    proc = lab.runner("a")
    for r in (old, recent):
        lab.wait_status(r, "completed")
    assert lab.terminate(proc) == 0
    sql(
        lab.db.admin_url,
        "UPDATE lab.research_runs SET ended_at = now() - interval '8 days' WHERE id = %s",
        (old,),
    )
    domain_before = sql(
        lab.db.admin_url, "SELECT id, status, error FROM lab.agent_tasks ORDER BY id"
    )

    def checkpoints(r: uuid.UUID) -> int:
        (count,) = sql(
            lab.db.admin_url,
            "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s",
            (f"run-{r}",),
        )[0]
        assert isinstance(count, int)
        return count

    before_recent = checkpoints(recent)
    assert checkpoints(old) > 1
    async with open_checkpointer(lab.settings.database) as saver:
        assert await prune_finished_runs(saver, lab.factory, older_than=timedelta(days=7)) == 1
        assert await prune_finished_runs(saver, lab.factory, older_than=timedelta(days=7)) == 0
        graph = selftest.build().compile(checkpointer=saver)
        state = await graph.aget_state({"configurable": {"thread_id": f"run-{old}"}})
    assert checkpoints(old) == 1 and checkpoints(recent) == before_recent
    assert state.values["log"][-1] == "finish: done"  # the final state is still readable
    assert (
        sql(lab.db.admin_url, "SELECT id, status, error FROM lab.agent_tasks ORDER BY id")
        == domain_before
    )
