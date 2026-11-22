"""Throwaway graphs with deterministic nodes. Each node records what it did in `spike.effects`,
so tests can count exactly how often every node ran, across processes and crashes.

Crash and failure points are injected through the run context (`Ctx`), never through graph state,
so the same thread can be resumed with the fault removed.
"""

from __future__ import annotations

import asyncio
import itertools
import operator
import os
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, TypedDict

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

HANG_SECONDS = 3600


class State(TypedDict, total=False):
    thread: str
    steps: Annotated[list[str], operator.add]
    results: Annotated[list[int], operator.add]


class BranchInput(TypedDict):
    thread: str
    i: int


@dataclass
class Ctx:
    pool: AsyncConnectionPool
    dsn: str  # same database as `pool`, for sync nodes
    hang_at: frozenset[str] = frozenset()  # points that block until the process is killed/cancelled
    fail_at: frozenset[str] = frozenset()  # points that raise
    delay: float = 0.0  # per-node work time
    fanout: int = 5
    extra: dict[str, Any] = field(default_factory=dict)


async def effect(ctx: Ctx, thread: str, node: str, event: str) -> None:
    async with ctx.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO spike.effects (thread, node, event, os_pid) VALUES (%s, %s, %s, %s)",
            (thread, node, event, os.getpid()),
        )


async def checkpoint_hazard(ctx: Ctx, thread: str, point: str) -> None:
    if point in ctx.fail_at:
        await effect(ctx, thread, point, "failed")
        raise RuntimeError(f"injected failure at {point}")
    if point in ctx.hang_at:
        await effect(ctx, thread, point, "hanging")
        await asyncio.sleep(HANG_SECONDS)


class NodeFn(Protocol):
    def __call__(
        self, state: State, *, runtime: Runtime[Ctx]
    ) -> Coroutine[Any, Any, dict[str, Any]]: ...


def simple(name: str) -> NodeFn:
    async def node(state: State, runtime: Runtime[Ctx]) -> dict[str, Any]:
        ctx = runtime.context
        await effect(ctx, state["thread"], name, "start")
        if ctx.delay:
            await asyncio.sleep(ctx.delay)
        await checkpoint_hazard(ctx, state["thread"], name)
        await effect(ctx, state["thread"], name, "done")
        return {"steps": [name]}

    return node


async def agent(state: State, runtime: Runtime[Ctx]) -> dict[str, Any]:
    """Idempotent agent node (ARCHITECTURE §6.3 step 2, §6.7): a re-execution after a crash
    never repeats a completed model call and never creates a second logical task."""
    ctx, thread = runtime.context, state["thread"]
    key = f"{thread}:agent"
    await effect(ctx, thread, "agent", "start")
    async with ctx.pool.connection() as conn:
        row = await (
            await conn.execute("SELECT status, result FROM spike.tasks WHERE key = %s", (key,))
        ).fetchone()
        if row and row[0] == "succeeded":
            await effect(ctx, thread, "agent", "reused_result")
            return {"steps": [row[1]]}
        await conn.execute(
            "INSERT INTO spike.tasks (key, status) VALUES (%s, 'running') ON CONFLICT DO NOTHING",
            (key,),
        )
        # In 2b the runner reaper does this from expired heartbeats; here the next attempt does.
        await conn.execute(
            "UPDATE spike.attempts SET status = 'abandoned' WHERE key = %s AND status = 'running'",
            (key,),
        )
        attempt = (
            await (
                await conn.execute(
                    "INSERT INTO spike.attempts (key, status, os_pid) "
                    "VALUES (%s, 'running', %s) RETURNING id",
                    (key, os.getpid()),
                )
            ).fetchone()
        )[0]  # type: ignore[index]
        # "Model call", cached by (task, call_seq): never paid twice.
        cached = await (
            await conn.execute(
                "SELECT result FROM spike.model_calls WHERE key = %s AND seq = 1", (key,)
            )
        ).fetchone()
        if cached:
            result = cached[0]
            await effect(ctx, thread, "agent", "model_cache_hit")
        else:
            result = "agent-answer"
            await conn.execute(
                "INSERT INTO spike.model_calls (key, seq, result) VALUES (%s, 1, %s)", (key, result)
            )
            await effect(ctx, thread, "agent", "model_called")
    await checkpoint_hazard(ctx, thread, "agent:before_commit")
    async with ctx.pool.connection() as conn, conn.transaction():
        await conn.execute(
            "UPDATE spike.tasks SET status = 'succeeded', result = %s WHERE key = %s", (result, key)
        )
        await conn.execute(
            "UPDATE spike.attempts SET status = 'succeeded' WHERE id = %s", (attempt,)
        )
    await checkpoint_hazard(ctx, thread, "agent:after_commit")
    await effect(ctx, thread, "agent", "done")
    return {"steps": [result]}


async def gate(state: State, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    await effect(ctx, state["thread"], "gate", "start")
    answer = interrupt({"question": "approve the next stage?", "thread": state["thread"]})
    await effect(ctx, state["thread"], "gate", f"resumed:{answer}")
    return {"steps": [f"gate:{answer}"]}


def plan(state: State) -> dict[str, Any]:
    return {"steps": ["plan"]}


def fan_out(state: State, runtime: Runtime[Ctx]) -> list[Send]:
    n = runtime.context.fanout
    return [Send("branch", {"thread": state["thread"], "i": i}) for i in range(n)]


async def branch(inp: BranchInput, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx, thread, name = runtime.context, inp["thread"], f"branch{inp['i']}"
    await effect(ctx, thread, name, "start")
    if name in ctx.extra.get("fail_fast", ()):
        await effect(ctx, thread, name, "failed")
        raise RuntimeError(f"injected failure at {name}")
    if ctx.delay:
        await asyncio.sleep(ctx.delay)
    if name in ctx.fail_at:
        await asyncio.sleep(0.5)  # a "late" failure: siblings finish well before it
    await checkpoint_hazard(ctx, thread, name)
    await effect(ctx, thread, name, "done")
    return {"results": [inp["i"]]}


def sync_worker(state: State, runtime: Runtime[Ctx]) -> dict[str, Any]:
    """A *synchronous* node (LangGraph runs it in a thread). Used to show what cancellation can
    and cannot interrupt."""
    ctx = runtime.context
    with psycopg.connect(ctx.dsn, autocommit=True) as conn:
        for i in range(int(ctx.extra.get("ticks", 20))):
            time.sleep(0.1)
            conn.execute(
                "INSERT INTO spike.effects (thread, node, event, os_pid) VALUES (%s, %s, %s, %s)",
                (state["thread"], "sync_worker", f"tick{i}", os.getpid()),
            )
    return {"steps": ["sync_worker"]}


def build(kind: str) -> StateGraph[State, Ctx]:
    g: StateGraph[State, Ctx] = StateGraph(State, context_schema=Ctx)
    if kind == "linear":  # a → b → agent → d
        for n in ("a", "b", "d"):
            g.add_node(n, simple(n))
        g.add_node("agent", agent)
        chain = [START, "a", "b", "agent", "d", END]
    elif kind == "approval":  # a → gate (interrupt) → d
        g.add_node("a", simple("a"))
        g.add_node("gate", gate)
        g.add_node("d", simple("d"))
        chain = [START, "a", "gate", "d", END]
    elif kind == "fanout":  # plan → N x branch (Send) → join
        g.add_node("plan", plan)
        g.add_node("branch", branch)  # type: ignore[arg-type]
        g.add_node("join", simple("join"))
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", fan_out, ["branch"])
        g.add_edge("branch", "join")
        g.add_edge("join", END)
        return g
    elif kind == "sync":  # a → sync_worker → d
        g.add_node("a", simple("a"))
        g.add_node("sync_worker", sync_worker)
        g.add_node("d", simple("d"))
        chain = [START, "a", "sync_worker", "d", END]
    else:
        raise ValueError(kind)
    for src, dst in itertools.pairwise(chain):
        g.add_edge(src, dst)
    return g


@asynccontextmanager
async def open_saver(graph_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Checkpointer on its own role and schema. The tables are unqualified in the library, so
    the schema comes from search_path."""
    async with await psycopg.AsyncConnection.connect(
        graph_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=langgraph",
    ) as conn:
        yield AsyncPostgresSaver(conn)


@asynccontextmanager
async def open_pool(domain_url: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = AsyncConnectionPool(
        domain_url, min_size=1, max_size=8, open=False, kwargs={"autocommit": True}
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def config(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def initial(thread: str) -> State:
    return {"thread": thread, "steps": []}
