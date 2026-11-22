"""The runner process: executes research runs claimed from the job queue (ARCHITECTURE §3.2).

    python -m src.services.runner

One process runs up to `runner.concurrency` runs at a time, each as a Procrastinate job holding the
lock of its LangGraph thread (I-24). Besides the jobs, it runs:

- heartbeats in `lab.runners` (Procrastinate keeps its own worker heartbeats as well);
- a control listener on `NOTIFY lab_control`, with a polling backup for missed messages:
  pause → drain the run after its current node (C-1); stop or kill switch → cancel it;
- the stalled-job reaper, a periodic job (C-3): hands back runs of dead runners and retries their
  jobs, so another runner continues them from their last checkpoint;
- checkpoint pruning of long-finished runs, a periodic job (criterion 2.11).

On SIGTERM it stops taking jobs, drains its runs (each is requeued for another runner after its
current node) and exits. Nodes still running after `runner.shutdown_timeout_s` are cut off; the
reaper then hands those runs back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
from procrastinate.worker import Worker
from sqlalchemy import select

from src import VERSION
from src.agents.checkpointer import Checkpointer, open_checkpointer, prune_finished_runs
from src.agents.runtime import RunExecution
from src.core.clock import utcnow
from src.core.config import Settings, get_settings
from src.core.databases.database import (
    create_engine,
    create_session_factory,
    libpq_url,
    session_scope,
)
from src.core.databases.models.runs import ResearchRun
from src.core.events.store import Actor, EventContext
from src.core.events.system import PROCESS_STARTED, PROCESS_STOPPED, record_lifecycle
from src.core.ids import uuid7
from src.core.logging import configure_logging, get_logger
from src.runs import recovery, runners
from src.runs.dispatch import (
    EXECUTE_RUN_TASK,
    RUN_QUEUE,
    ProcrastinateDispatcher,
    make_job_app,
)
from src.runs.service import CONTROL_CHANNEL

log = get_logger(__name__)

MAINTENANCE_QUEUE = "maintenance"
REAPER_TASK = "lab.reap_stalled_jobs"
PRUNE_TASK = "lab.prune_checkpoints"


@dataclass
class _Active:
    execution: RunExecution
    task: asyncio.Task[Any]


class Runner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.id = uuid7()
        self.engine = create_engine(settings.database)
        self.factory = create_session_factory(self.engine)
        self.app = make_job_app(settings.database)
        self.dispatcher = ProcrastinateDispatcher(self.app)
        self.active: dict[uuid.UUID, _Active] = {}
        self._saver: Checkpointer | None = None
        self._stopping = asyncio.Event()
        self._actor = EventContext(Actor("system", f"runner:{self.id}"))
        self._register_tasks()

    # --- jobs ------------------------------------------------------------------------------

    def _register_tasks(self) -> None:
        cfg = self.settings.runner

        @self.app.task(name=EXECUTE_RUN_TASK, queue=RUN_QUEUE)
        async def execute_run(run_id: str) -> None:
            await self._execute(uuid.UUID(run_id))

        @self.app.periodic(cron=f"* * * * * */{cfg.reaper_interval_s}", periodic_id="reaper")
        @self.app.task(name=REAPER_TASK, queue=MAINTENANCE_QUEUE, queueing_lock=REAPER_TASK)
        async def reap(timestamp: int) -> None:
            await self.reap_stalled_jobs()

        @self.app.periodic(cron=cfg.prune_cron, periodic_id="prune")
        @self.app.task(name=PRUNE_TASK, queue=MAINTENANCE_QUEUE, queueing_lock=PRUNE_TASK)
        async def prune(timestamp: int) -> None:
            await self.prune_checkpoints()

    async def _execute(self, run_id: uuid.UUID) -> None:
        assert self._saver is not None
        execution = RunExecution(
            run_id,
            factory=self.factory,
            checkpointer=self._saver,
            runner_id=self.id,
            dispatcher=self.dispatcher,
            limits=self.settings.limits,
        )
        inner = asyncio.create_task(execution.execute(), name=f"run:{run_id}")
        self.active[run_id] = _Active(execution, inner)
        if self._stopping.is_set():
            execution.request_shutdown()
        try:
            # Wait without propagating: a stop cancels `inner`, which ends this job normally.
            await asyncio.wait({inner})
        except asyncio.CancelledError:
            # The job itself is being aborted (shutdown timeout): cut the run off as well.
            inner.cancel()
            await asyncio.gather(inner, return_exceptions=True)
            raise
        finally:
            self.active.pop(run_id, None)
        if not inner.cancelled():
            outcome = inner.result()
            log.info("run_execution_ended", run_id=str(run_id), outcome=outcome.value)

    async def reap_stalled_jobs(self) -> None:
        cfg = self.settings.runner
        stalled = list(
            await self.app.job_manager.get_stalled_jobs(seconds_since_heartbeat=cfg.stall_timeout_s)
        )
        for job in stalled:
            if job.task_name == EXECUTE_RUN_TASK:
                async with session_scope(self.factory) as session:
                    status = await recovery.recover_lost_run(
                        session, uuid.UUID(str(job.task_kwargs["run_id"])), context=self._actor
                    )
                log.warning("run_recovered", run_id=job.task_kwargs["run_id"], status=status)
            await self.app.job_manager.retry_job(job)
        async with session_scope(self.factory) as session:
            dead = await recovery.mark_dead_runners(
                session, stale_before=utcnow() - timedelta(seconds=cfg.stall_timeout_s)
            )
        if dead or stalled:
            log.warning("reaper", stalled_jobs=len(stalled), dead_runners=[str(d) for d in dead])

    async def prune_checkpoints(self) -> int:
        assert self._saver is not None
        days = self.settings.runner.checkpoint_retention_days
        return await prune_finished_runs(self._saver, self.factory, older_than=timedelta(days=days))

    # --- control ---------------------------------------------------------------------------

    def _apply(self, action: str, run_id: uuid.UUID | None) -> None:
        targets = list(self.active) if run_id is None else [run_id]
        for rid in targets:
            active = self.active.get(rid)
            if active is None:
                continue
            if action == "pause":
                active.execution.request_pause()
            elif action in {"stop", "kill"}:
                active.task.cancel()

    async def _listen_for_control(self) -> None:
        dsn = libpq_url(self.settings.database.url.get_secret_value())
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {CONTROL_CHANNEL}")
                    await self._poll_control()  # catch up on anything missed while disconnected
                    async for note in conn.notifies():
                        message = json.loads(note.payload)
                        run_id = message.get("run_id")
                        self._apply(message["action"], uuid.UUID(run_id) if run_id else None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("control_listener_failed")
                await asyncio.sleep(1)

    async def _poll_control(self) -> None:
        if not self.active:
            return
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(ResearchRun.id, ResearchRun.status, ResearchRun.pause_requested).where(
                        ResearchRun.id.in_(list(self.active))
                    )
                )
            ).all()
        for run_id, status, pause_requested in rows:
            if status == "cancel_requested":
                self._apply("stop", run_id)
            elif pause_requested:
                self._apply("pause", run_id)

    async def _poll_control_forever(self) -> None:
        while True:
            await asyncio.sleep(self.settings.runner.control_poll_interval_s)
            try:
                await self._poll_control()
            except Exception:
                log.exception("control_poll_failed")

    async def _heartbeat_forever(self) -> None:
        while True:
            await asyncio.sleep(self.settings.runner.heartbeat_interval_s)
            try:
                async with session_scope(self.factory) as session:
                    await runners.heartbeat(session, self.id)
            except Exception:
                log.exception("heartbeat_failed")

    # --- lifecycle -------------------------------------------------------------------------

    def request_stop(self) -> None:
        if not self._stopping.is_set():
            log.info("runner_stopping", runner_id=str(self.id), active_runs=len(self.active))
            self._stopping.set()

    async def run(self) -> None:
        cfg = self.settings.runner
        async with (
            self.app.open_async(),
            open_checkpointer(self.settings.database, max_size=cfg.concurrency + 2) as saver,
        ):
            self._saver = saver
            async with session_scope(self.factory) as session:
                await runners.register(
                    session,
                    self.id,
                    capabilities={"version": VERSION, "concurrency": cfg.concurrency},
                )
            worker = Worker(
                app=self.app,
                queues=[RUN_QUEUE, MAINTENANCE_QUEUE],
                name=f"runner-{self.id}",
                concurrency=cfg.concurrency + 1,  # one slot for maintenance jobs
                install_signal_handlers=False,
                update_heartbeat_interval=cfg.heartbeat_interval_s,
                stalled_worker_timeout=cfg.stall_timeout_s,
                shutdown_graceful_timeout=cfg.shutdown_timeout_s,
            )
            side = [
                asyncio.create_task(self._heartbeat_forever(), name="heartbeat"),
                asyncio.create_task(self._listen_for_control(), name="control"),
                asyncio.create_task(self._poll_control_forever(), name="control-poll"),
            ]
            # Procrastinate leaves Worker.run/stop unannotated.
            worker_task = asyncio.create_task(worker.run(), name="worker")  # type: ignore[no-untyped-call]
            log.info("runner_started", runner_id=str(self.id), concurrency=cfg.concurrency)
            stop_wait = asyncio.create_task(self._stopping.wait())
            await asyncio.wait({worker_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED)

            async with session_scope(self.factory) as session:
                await runners.set_status(session, self.id, "stopping")
            for active in list(self.active.values()):
                active.execution.request_shutdown()  # finish the current node, then hand back
            worker.stop()  # type: ignore[no-untyped-call]
            await asyncio.gather(worker_task, return_exceptions=True)
            for task in [*side, stop_wait]:
                task.cancel()
            await asyncio.gather(*side, stop_wait, return_exceptions=True)
            async with session_scope(self.factory) as session:
                await runners.set_status(session, self.id, "stopped")
        await self.engine.dispose()
        log.info("runner_stopped", runner_id=str(self.id))


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.logging)
    runner = Runner(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, runner.request_stop)
    await record_lifecycle(runner.factory, PROCESS_STARTED.name, "runner")
    try:
        await runner.run()
    finally:
        engine = create_engine(settings.database)
        with contextlib.suppress(Exception):
            await record_lifecycle(create_session_factory(engine), PROCESS_STOPPED.name, "runner")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["Runner", "main"]
