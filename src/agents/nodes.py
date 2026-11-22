"""Helpers for graph nodes: task records, approvals and bounded loops."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.types import interrupt

from src.agents.context import RunContext
from src.core.databases.database import session_scope
from src.core.events.store import Actor, EventContext
from src.runs import approvals
from src.runs.tasks import Result, run_task, task_key


async def task(
    ctx: RunContext,
    node_path: str,
    work: Callable[[], Awaitable[Result]],
    *,
    fanout_key: str | None = None,
    role_key: str | None = None,
    goal: str | None = None,
) -> Result:
    """Run `work` as this graph position's logical task (done at most once, see src.runs.tasks)."""
    return await run_task(
        ctx.factory,
        run_id=ctx.run_id,
        thread_id=ctx.thread_id,
        node_path=node_path,
        work=work,
        fanout_key=fanout_key,
        runner_id=ctx.runner_id,
        role_key=role_key,
        goal=goal,
    )


async def ask_approval(
    ctx: RunContext,
    node_path: str,
    *,
    kind: str,
    payload: dict[str, Any],
    fanout_key: str | None = None,
) -> dict[str, Any]:
    """Pause the run until a person decides. Returns {"approved": bool, "comment": str | None}.

    Everything before this call in the node runs again on resume, so it must be idempotent (C-5).
    The request itself is: it is keyed by the node's position.
    """
    key = f"{task_key(ctx.thread_id, node_path, fanout_key)}:approval:{kind}"
    async with session_scope(ctx.factory) as session:
        approval = await approvals.request_approval(
            session,
            run_id=ctx.run_id,
            request_key=key,
            kind=kind,
            payload=payload,
            context=EventContext(Actor("agent", node_path)),
        )
    answer = interrupt({"approval_id": str(approval.id), "kind": kind, "payload": payload})
    return dict(answer)


def bounded(count: int, limit: int, *, again: str, done: str) -> str:
    """Route of a rework loop: repeat while under the configured limit, then leave (2.9).

    The graph's recursion limit is only a backstop; this is the real bound.
    """
    return again if count < limit else done
