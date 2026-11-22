"""Executes one run of a registered graph and records the outcome (ARCHITECTURE §6.6, §6.7).

| LangGraph outcome | Run status |
|---|---|
| finished | `completed` |
| `GraphDrained` (pause, DECISIONS C-1) | `paused`; nothing holds a runner while paused |
| `GraphDrained` (runner shutdown) | `queued` again, with a new job: another runner continues |
| pending interrupts (approval) | `awaiting_approval`; nothing holds a runner while waiting |
| the asyncio task is cancelled (stop) | `stopped`, if a stop was requested |
| an exception | `failed`, with the error |

If a stop was requested while the run was pausing or reaching an approval, it ends `stopped`.

What to invoke the graph with is derived from its checkpoint, never from guesses:
- no checkpoint yet: the graph's initial state from the run's parameters;
- pending interrupts: `Command(resume={interrupt_id: answer})` for the decided approvals;
- otherwise (resume after a pause or a crash): `None`, continuing from the last checkpoint.

Every invocation uses `durability="sync"` (C-4).
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl
from langgraph.types import Command, Interrupt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents import graphs as _builtin_graphs  # noqa: F401  # registers built-ins
from src.agents import registry
from src.agents.context import RunContext
from src.core.config import LimitSettings
from src.core.databases.database import session_scope
from src.core.errors import NotFound
from src.core.events.store import Actor, EventContext
from src.core.logging import get_logger
from src.runs import approvals, budgets, service
from src.runs.dispatch import Dispatcher
from src.runs.tasks import TaskLimits

log = get_logger(__name__)

RECURSION_LIMIT = 200  # backstop only; loops are bounded explicitly (src.agents.nodes.bounded)


class Outcome(enum.StrEnum):
    COMPLETED = "completed"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPED = "stopped"
    FAILED = "failed"
    REQUEUED = "requeued"  # drained for a runner shutdown; another runner continues
    SKIPPED = "skipped"  # the run was not queued when this execution picked it up


def _pending_interrupts(snapshot: Any) -> list[Interrupt]:
    return [i for t in snapshot.tasks for i in t.interrupts]


class RunExecution:
    """One execution of one run. The runner calls `request_pause()` on a pause request,
    `request_shutdown()` when it is shutting down, and cancels the task running `execute()` on a
    stop."""

    def __init__(
        self,
        run_id: uuid.UUID,
        *,
        factory: async_sessionmaker[AsyncSession],
        checkpointer: BaseCheckpointSaver[Any],
        runner_id: uuid.UUID | None = None,
        dispatcher: Dispatcher | None = None,
        limits: LimitSettings | None = None,
    ) -> None:
        self.run_id = run_id
        self._limits = limits or LimitSettings()
        self._dispatcher = dispatcher
        self._factory = factory
        self._checkpointer = checkpointer
        self._runner_id = runner_id
        self._control = RunControl()
        self._actor = EventContext(
            Actor("system", f"runner:{runner_id}" if runner_id else "runner")
        )

    def request_pause(self) -> None:
        """Stop after the node that is running now, with its checkpoint saved (C-1)."""
        self._control.request_drain("pause")

    def request_shutdown(self) -> None:
        """Stop after the current node and hand the run back to the queue (runner shutdown)."""
        self._control.request_drain("shutdown")

    async def _claim(self) -> tuple[str, str, str, dict[str, Any]] | None:
        async with session_scope(self._factory) as session:
            run = await service.lock_run(session, self.run_id)
            if run.status != "queued":
                log.info("run_not_runnable", run_id=str(run.id), status=run.status)
                return None
            run.runner_id = self._runner_id
            await service.transition(session, run, "running", context=self._actor)
            return run.thread_id, run.graph_key, run.graph_version, dict(run.params)

    async def _finish(self, outcome: Outcome, *, error: str | None = None) -> Outcome:
        async with session_scope(self._factory) as session:
            run = await service.lock_run(session, self.run_id)
            if run.status == "cancel_requested" and outcome in {
                Outcome.PAUSED,
                Outcome.AWAITING_APPROVAL,
                Outcome.STOPPED,
                Outcome.REQUEUED,
            }:
                outcome = Outcome.STOPPED  # a stop wins over a pause, an approval or a hand-back
            elif outcome is Outcome.STOPPED:
                # Cancelled without a stop request (the runner's shutdown timeout expired): leave
                # the run as it is; the stalled-job reaper requeues it (C-3).
                return outcome
            elif outcome is Outcome.REQUEUED:
                if self._dispatcher is None:
                    raise RuntimeError("a requeue needs a dispatcher")
                run.runner_id = None
                await service.transition(
                    session, run, "queued", context=self._actor, reason="runner shutdown"
                )
                await self._dispatcher.enqueue_run(session, run_id=run.id, thread_id=run.thread_id)
                return outcome
            if error:
                run.error = error
            run.runner_id = None
            await service.transition(session, run, outcome.value, context=self._actor)
            return outcome

    async def _context(self, thread_id: str) -> RunContext:
        cfg = self._limits
        async with self._factory() as session:
            budget_parallel = await budgets.parallel_limit(session, self.run_id)
        parallel = min(cfg.run_parallelism, budget_parallel or cfg.run_parallelism)
        return RunContext(
            self.run_id,
            thread_id,
            self._factory,
            self._runner_id,
            limits=TaskLimits(
                timeout_s=cfg.task_timeout_s,
                max_retries=cfg.max_retries,
                backoff_initial_s=cfg.backoff_initial_s,
                backoff_max_s=cfg.backoff_max_s,
            ),
            slots=asyncio.Semaphore(parallel),
        )

    async def _graph_input(
        self, graph: Any, config: RunnableConfig, initial: dict[str, Any]
    ) -> Any:
        snapshot = await graph.aget_state(config)
        if snapshot.created_at is None:  # no checkpoint: first execution
            return initial
        pending = _pending_interrupts(snapshot)
        if not pending:
            return None  # continue from the last checkpoint
        by_approval = {uuid.UUID(i.value["approval_id"]): i.id for i in pending}
        async with self._factory() as session:
            answers = await approvals.decided_responses(session, list(by_approval))
        return Command(resume={by_approval[a]: answer for a, answer in answers.items()})

    async def execute(self) -> Outcome:
        claimed = await self._claim()
        if claimed is None:
            return Outcome.SKIPPED
        thread_id, graph_key, graph_version, params = claimed
        try:
            definition = registry.get(graph_key, graph_version)
            initial = definition.initial_state(definition.params_model.model_validate(params))
        except (NotFound, ValueError) as exc:
            return await self._finish(Outcome.FAILED, error=str(exc))

        graph = definition.build().compile(checkpointer=self._checkpointer)
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }
        context = await self._context(thread_id)
        try:
            await graph.ainvoke(
                await self._graph_input(graph, config, initial),
                config,
                context=context,
                durability="sync",
                control=self._control,
            )
        except GraphDrained:
            if self._control.drain_reason == "shutdown":
                return await self._finish(Outcome.REQUEUED)
            return await self._finish(Outcome.PAUSED)
        except asyncio.CancelledError:
            await asyncio.shield(self._finish(Outcome.STOPPED))
            raise
        except Exception as exc:
            log.exception("run_failed", run_id=str(self.run_id))
            return await self._finish(Outcome.FAILED, error=f"{type(exc).__name__}: {exc}")

        if _pending_interrupts(await graph.aget_state(config)):
            return await self._finish(Outcome.AWAITING_APPROVAL)
        return await self._finish(Outcome.COMPLETED)
