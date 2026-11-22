"""Executes one run of a registered graph and records the outcome (ARCHITECTURE §6.6, §6.7).

| LangGraph outcome | Run status |
|---|---|
| finished | `completed` |
| `GraphDrained` (pause, DECISIONS C-1) | `paused`; nothing holds a runner while paused |
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
from src.core.databases.database import session_scope
from src.core.errors import NotFound
from src.core.events.store import Actor, EventContext
from src.core.logging import get_logger
from src.runs import approvals, service

log = get_logger(__name__)

RECURSION_LIMIT = 200  # backstop only; loops are bounded explicitly (src.agents.nodes.bounded)


class Outcome(enum.StrEnum):
    COMPLETED = "completed"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPED = "stopped"
    FAILED = "failed"
    SKIPPED = "skipped"  # the run was not queued when this execution picked it up


def _pending_interrupts(snapshot: Any) -> list[Interrupt]:
    return [i for t in snapshot.tasks for i in t.interrupts]


class RunExecution:
    """One execution of one run. The runner (2b.4) calls `request_pause()` on a pause request and
    cancels the task running `execute()` on a stop."""

    def __init__(
        self,
        run_id: uuid.UUID,
        *,
        factory: async_sessionmaker[AsyncSession],
        checkpointer: BaseCheckpointSaver[Any],
        runner_id: uuid.UUID | None = None,
    ) -> None:
        self.run_id = run_id
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
            }:
                outcome = Outcome.STOPPED  # a stop wins over a pause or an approval wait
            elif outcome is Outcome.STOPPED:
                # Cancelled without a stop request (for example a runner shutdown): leave the run
                # as it is; recovery requeues it (2b.4).
                return outcome
            if error:
                run.error = error
            run.runner_id = None
            await service.transition(session, run, outcome.value, context=self._actor)
            return outcome

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
        context = RunContext(self.run_id, thread_id, self._factory, self._runner_id)
        try:
            await graph.ainvoke(
                await self._graph_input(graph, config, initial),
                config,
                context=context,
                durability="sync",
                control=self._control,
            )
        except GraphDrained:
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
