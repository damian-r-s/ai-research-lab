"""Phase 2 step 2b.3: graph runtime (criteria 2.5, 2.6, 2.9; 2.4 stop latency in-process).

Real PostgreSQL and real LangGraph checkpoints. A "different runner" is a new executor with its own
checkpointer pool and database engine; the multi-process version (kill -9) is part of 2b.4.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, TypedDict

import pytest
import pytest_asyncio
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents import registry
from src.agents.checkpointer import open_checkpointer, setup_checkpointer
from src.agents.graphs import selftest
from src.agents.runtime import Outcome, RunExecution
from src.core.config import Settings
from src.core.databases.database import create_engine, create_session_factory, session_scope
from src.core.databases.models.runs import AgentTask, AgentTaskAttempt, Approval, ResearchRun
from src.core.events.store import Actor, EventContext
from src.runs import approvals, service
from src.runs.dispatch import ProcrastinateDispatcher, make_job_app
from test.conftest import ScratchDatabase, settings_for, sql

pytestmark = pytest.mark.integration

USER = EventContext(Actor("user", "tester"))


class Lab:
    def __init__(
        self, db: ScratchDatabase, settings: Settings, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.db, self.settings, self.factory = db, settings, factory
        self.dispatcher = ProcrastinateDispatcher(make_job_app(settings.database))

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

    async def execute(self, run_id: uuid.UUID) -> Outcome:
        """A fresh 'runner': its own engine and checkpointer pool."""
        engine = create_engine(self.settings.database)
        try:
            async with open_checkpointer(self.settings.database) as saver:
                return await RunExecution(
                    run_id, factory=create_session_factory(engine), checkpointer=saver
                ).execute()
        finally:
            await engine.dispose()

    async def run(self, run_id: uuid.UUID) -> ResearchRun:
        async with self.factory() as s:
            run = await s.get(ResearchRun, run_id)
            assert run is not None
            return run

    async def tasks(self, run_id: uuid.UUID) -> dict[str, tuple[str, list[str | None]]]:
        """node_path[:fanout] -> (task status, attempt outcomes)."""
        async with self.factory() as s:
            rows = (await s.scalars(select(AgentTask).where(AgentTask.run_id == run_id))).all()
            result = {}
            for t in rows:
                attempts = (
                    await s.scalars(
                        select(AgentTaskAttempt.outcome)
                        .where(AgentTaskAttempt.agent_task_id == t.id)
                        .order_by(AgentTaskAttempt.attempt_no)
                    )
                ).all()
                name = t.idempotency_key.split(":", 1)[1].rstrip(":")
                result[name] = (t.status, list(attempts))
            return result

    async def wait_for_task(
        self, run_id: uuid.UUID, name: str, status: str, limit_s: float = 10
    ) -> None:
        deadline = time.monotonic() + limit_s
        while (await self.tasks(run_id)).get(name, ("", []))[0] != status:
            if time.monotonic() > deadline:
                raise TimeoutError((name, status, await self.tasks(run_id)))
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def lab(fresh_db: ScratchDatabase) -> AsyncIterator[Lab]:
    settings = settings_for(fresh_db)
    await setup_checkpointer(settings.database)
    engine = create_engine(settings.database)
    try:
        yield Lab(fresh_db, settings, create_session_factory(engine))
    finally:
        await engine.dispose()


def all_succeeded_once(tasks: dict[str, tuple[str, list[str | None]]]) -> bool:
    return all(
        status == "succeeded" and attempts == ["succeeded"] for status, attempts in tasks.values()
    )


# --- C-2: async-only graphs -----------------------------------------------------------------


class S(TypedDict, total=False):
    x: int


async def _async_node(state: S) -> dict[str, Any]:
    return {}


def _sync_node(state: S) -> dict[str, Any]:
    return {}


def test_a_graph_with_a_sync_node_is_rejected() -> None:
    g: StateGraph[S, Any, Any, Any] = StateGraph(S)
    g.add_node("ok", _async_node)
    g.add_node("blocking", _sync_node)
    g.add_edge(START, "ok")
    g.add_edge("ok", "blocking")
    g.add_edge("blocking", END)
    with pytest.raises(registry.SyncNodeError, match="blocking"):
        registry.assert_async_nodes(g)


def test_a_sync_node_inside_a_subgraph_is_rejected() -> None:
    inner: StateGraph[S, Any, Any, Any] = StateGraph(S)
    inner.add_node("hidden", _sync_node)
    inner.add_edge(START, "hidden")
    outer: StateGraph[S, Any, Any, Any] = StateGraph(S)
    outer.add_node("sub", inner.compile())
    outer.add_edge(START, "sub")
    with pytest.raises(registry.SyncNodeError, match="sub/hidden"):
        registry.assert_async_nodes(outer)


def test_every_registered_graph_is_async() -> None:
    assert registry.get(selftest.KEY, selftest.VERSION) is selftest.SELFTEST
    for definition in registry.all_graphs():
        registry.assert_async_nodes(definition.build())


# --- completion, failure, bounded loops ---------------------------------------------------


async def test_a_run_completes_with_one_successful_attempt_per_task(lab: Lab) -> None:
    run_id = await lab.start(steps=3, fanout=3)
    assert await lab.execute(run_id) is Outcome.COMPLETED
    run = await lab.run(run_id)
    assert run.status == "completed" and run.runner_id is None and run.ended_at is not None
    tasks = await lab.tasks(run_id)
    assert set(tasks) == {
        "plan",
        "probe:0",
        "probe:1",
        "probe:2",
        "step:1",
        "step:2",
        "step:3",
        "review:0",
        "finish",
    }
    assert all_succeeded_once(tasks)


async def test_rework_loop_is_bounded_by_configuration(lab: Lab) -> None:
    """Criterion 2.9: a reviewer that always asks for rework cannot loop forever."""
    run_id = await lab.start(steps=1, review_rejections=100)
    assert await lab.execute(run_id) is Outcome.COMPLETED
    tasks = await lab.tasks(run_id)
    assert sorted(k for k in tasks if k.startswith("rework")) == ["rework:1", "rework:2"]
    assert sorted(k for k in tasks if k.startswith("review")) == [
        "review:0",
        "review:1",
        "review:2",
    ]
    assert selftest.MAX_REWORK_CYCLES == 2


async def test_a_failing_task_fails_the_run_with_its_error(lab: Lab) -> None:
    run_id = await lab.start(steps=3, fail_at_step=2)
    assert await lab.execute(run_id) is Outcome.FAILED
    run = await lab.run(run_id)
    assert run.status == "failed" and "step 2" in (run.error or "")
    tasks = await lab.tasks(run_id)
    assert tasks["step:1"] == ("succeeded", ["succeeded"])
    assert tasks["step:2"] == ("failed", ["failed"])
    assert "step:3" not in tasks


async def test_invalid_parameters_or_unknown_graphs_fail_cleanly(lab: Lab) -> None:
    bad_params = await lab.start(steps=0)
    assert await lab.execute(bad_params) is Outcome.FAILED
    async with session_scope(lab.factory) as s:
        unknown = await service.start_run(
            s,
            graph_key="nope",
            graph_version="1",
            params={},
            context=USER,
            dispatcher=lab.dispatcher,
        )
    assert await lab.execute(unknown.id) is Outcome.FAILED
    assert "not registered" in ((await lab.run(unknown.id)).error or "")


async def test_a_run_that_is_not_queued_is_skipped(lab: Lab) -> None:
    run_id = await lab.start()
    async with session_scope(lab.factory) as s:
        await service.request_stop(s, run_id, context=USER)
    assert await lab.execute(run_id) is Outcome.SKIPPED
    assert (await lab.run(run_id)).status == "stopped"


# --- pause and resume (2.5) ------------------------------------------------------------------


async def test_pause_suspends_after_the_current_node_and_resume_continues(lab: Lab) -> None:
    run_id = await lab.start(steps=4, delay_s=0.3)
    engine = create_engine(lab.settings.database)
    async with open_checkpointer(lab.settings.database) as saver:
        execution = RunExecution(run_id, factory=create_session_factory(engine), checkpointer=saver)
        running = asyncio.create_task(execution.execute())
        await lab.wait_for_task(run_id, "step:1", "running")
        async with session_scope(lab.factory) as s:
            await service.request_pause(s, run_id, context=USER)
        execution.request_pause()  # what the runner does on the NOTIFY (2b.4)
        assert await running is Outcome.PAUSED
    await engine.dispose()

    run = await lab.run(run_id)
    assert (run.status, run.pause_requested, run.runner_id) == ("paused", False, None)
    tasks = await lab.tasks(run_id)
    assert tasks["step:1"] == ("succeeded", ["succeeded"])  # the current node finished cleanly
    assert "step:2" not in tasks  # nothing after it started

    async with session_scope(lab.factory) as s:
        await service.resume(s, run_id, context=USER, dispatcher=lab.dispatcher)
    assert await lab.execute(run_id) is Outcome.COMPLETED  # another "runner"
    assert all_succeeded_once(await lab.tasks(run_id))  # nothing was repeated


# --- stop (2.4, in-process part) ---------------------------------------------------------------


async def test_stop_cancels_the_running_node_promptly(lab: Lab) -> None:
    run_id = await lab.start(steps=2, delay_s=30)
    engine = create_engine(lab.settings.database)
    async with open_checkpointer(lab.settings.database) as saver:
        running = asyncio.create_task(
            RunExecution(
                run_id, factory=create_session_factory(engine), checkpointer=saver
            ).execute()
        )
        await lab.wait_for_task(run_id, "plan", "running")
        async with session_scope(lab.factory) as s:
            await service.request_stop(s, run_id, context=USER, reason="test")
        assert (await lab.run(run_id)).status == "cancel_requested"
        started = time.monotonic()
        running.cancel()  # what the runner does on the NOTIFY (2b.4)
        with pytest.raises(asyncio.CancelledError):
            await running
        latency = time.monotonic() - started
    await engine.dispose()
    assert latency < 10.0, latency  # ARCHITECTURE §6.6 target
    run = await lab.run(run_id)
    assert (run.status, run.runner_id, run.stop_reason) == ("stopped", None, "test")
    assert (await lab.tasks(run_id))["plan"] == ("cancelled", ["cancelled"])


async def test_a_stop_during_a_pause_ends_the_run_stopped(lab: Lab) -> None:
    run_id = await lab.start(steps=3, delay_s=0.3)
    engine = create_engine(lab.settings.database)
    async with open_checkpointer(lab.settings.database) as saver:
        execution = RunExecution(run_id, factory=create_session_factory(engine), checkpointer=saver)
        running = asyncio.create_task(execution.execute())
        await lab.wait_for_task(run_id, "step:1", "running")
        async with session_scope(lab.factory) as s:
            await service.request_pause(s, run_id, context=USER)
            await service.request_stop(s, run_id, context=USER)
        execution.request_pause()
        assert await running is Outcome.STOPPED
    await engine.dispose()
    assert (await lab.run(run_id)).status == "stopped"


# --- approvals (2.6) ------------------------------------------------------------------------


async def _approval(lab: Lab, run_id: uuid.UUID) -> Approval:
    async with lab.factory() as s:
        rows = (await s.scalars(select(Approval).where(Approval.run_id == run_id))).all()
    assert len(rows) == 1  # one request, even though the node runs again on resume (C-5)
    return rows[0]


@pytest.mark.parametrize("approved", [True, False])
async def test_an_approval_waits_without_a_runner_and_resumes_elsewhere(
    lab: Lab, approved: bool
) -> None:
    run_id = await lab.start(steps=1, approval=True)
    assert await lab.execute(run_id) is Outcome.AWAITING_APPROVAL
    run = await lab.run(run_id)
    assert (run.status, run.runner_id) == ("awaiting_approval", None)
    approval = await _approval(lab, run_id)
    assert approval.status == "pending" and approval.kind == "selftest_gate"

    async with session_scope(lab.factory) as s:
        await approvals.decide(
            s, approval.id, approved=approved, comment="ok", context=USER, dispatcher=lab.dispatcher
        )
    assert (await lab.run(run_id)).status == "queued"
    assert await lab.execute(run_id) is Outcome.COMPLETED  # a different "runner"

    assert (await _approval(lab, run_id)).status == ("approved" if approved else "rejected")
    events = [t for (t,) in sql(lab.db.admin_url, "SELECT type FROM lab.events ORDER BY seq")]
    assert events.count("runs.approval.requested") == 1
    assert events.count("runs.approval.decided") == 1
    assert all_succeeded_once(await lab.tasks(run_id))


async def test_an_approval_cannot_be_decided_twice(lab: Lab) -> None:
    from src.core.errors import ConflictError

    run_id = await lab.start(steps=1, approval=True)
    await lab.execute(run_id)
    approval = await _approval(lab, run_id)
    async with session_scope(lab.factory) as s:
        await approvals.decide(
            s, approval.id, approved=True, comment=None, context=USER, dispatcher=lab.dispatcher
        )
    async with session_scope(lab.factory) as s:
        with pytest.raises(ConflictError):
            await approvals.decide(
                s,
                approval.id,
                approved=False,
                comment=None,
                context=USER,
                dispatcher=lab.dispatcher,
            )
