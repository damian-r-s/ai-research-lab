"""Agent task records (src.runs.tasks): the crash-safety rules, tested directly."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.databases.database import create_engine, create_session_factory, session_scope
from src.core.databases.models.runs import AgentTask, AgentTaskAttempt
from src.core.events.store import Actor, EventContext
from src.runs import service, tasks
from src.runs.dispatch import ProcrastinateDispatcher, make_job_app
from test.conftest import ScratchDatabase, settings_for

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def factory(fresh_db: ScratchDatabase) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_for(fresh_db).database)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def run_id(factory: async_sessionmaker[AsyncSession], fresh_db: ScratchDatabase) -> uuid.UUID:
    dispatcher = ProcrastinateDispatcher(make_job_app(settings_for(fresh_db).database))
    async with session_scope(factory) as s:
        run = await service.start_run(
            s,
            graph_key="x",
            graph_version="1",
            params={},
            context=EventContext(Actor("user", "t")),
            dispatcher=dispatcher,
        )
    return run.id


async def attempts(factory: async_sessionmaker[AsyncSession]) -> list[str | None]:
    async with factory() as s:
        return list(
            (
                await s.scalars(
                    select(AgentTaskAttempt.outcome).order_by(AgentTaskAttempt.attempt_no)
                )
            ).all()
        )


async def test_a_finished_task_is_reused_not_redone(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    calls = 0

    async def work() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    for _ in range(3):  # e.g. the node re-executed after crashes
        result = await tasks.run_task(
            factory, run_id=run_id, thread_id="t", node_path="n", work=work
        )
        assert result == {"answer": 42}
    assert calls == 1
    assert await attempts(factory) == ["succeeded"]


async def test_an_attempt_left_open_by_a_dead_execution_is_abandoned(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    # A previous execution opened an attempt and died before closing it.
    async with session_scope(factory) as s:
        await tasks._open_attempt(
            s,
            run_id=run_id,
            key=tasks.task_key("t", "n"),
            node_path="n",
            runner_id=None,
            role_key=None,
            goal=None,
            limits={},
        )

    async def work() -> dict[str, Any]:
        return {"ok": True}

    await tasks.run_task(factory, run_id=run_id, thread_id="t", node_path="n", work=work)
    assert await attempts(factory) == ["abandoned", "succeeded"]  # kept, never erased
    async with factory() as s:
        (task,) = (await s.scalars(select(AgentTask))).all()
    assert task.status == "succeeded"


async def test_fanout_keys_make_separate_tasks(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    for i in range(3):

        async def work(i: int = i) -> dict[str, Any]:
            return {"i": i}

        await tasks.run_task(
            factory, run_id=run_id, thread_id="t", node_path="probe", work=work, fanout_key=str(i)
        )
    async with factory() as s:
        keys = sorted((await s.scalars(select(AgentTask.idempotency_key))).all())
    assert keys == ["t:probe:0", "t:probe:1", "t:probe:2"]
