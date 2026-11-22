"""Procrastinate app for the A-7 checks, and a worker process entry point.

    python -m test.spike.jobs_app <worker-name>      (env: SPIKE_DOMAIN_URL, optional
                                                      SPIKE_HOLD_SECONDS, SPIKE_PERIODIC=1,
                                                      SPIKE_CONCURRENCY)

Every task records what it did in `spike.job_runs`, so tests can check exclusivity, retries,
periodic de-duplication and cancellation from the outside.
"""

from __future__ import annotations

import asyncio
import os
import sys

import procrastinate
import psycopg
from procrastinate import JobContext, RetryStrategy

QUEUE = "spike"
SEARCH_PATH = "-c search_path=jobs"  # Procrastinate's SQL is unqualified, like the checkpointer's


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def make_app(url: str, *, periodic: bool = False) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=url, kwargs={"options": SEARCH_PATH})
    )

    async def record(task: str, tag: str, event: str) -> None:
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(
                "INSERT INTO spike.job_runs (task, tag, worker, event) VALUES (%s, %s, %s, %s)",
                (task, tag, os.environ.get("SPIKE_WORKER", "test"), event),
            )

    @app.task(name="hold", queue=QUEUE)
    async def hold(tag: str) -> None:
        await record("hold", tag, "start")
        await asyncio.sleep(float(os.environ.get("SPIKE_HOLD_SECONDS", "0.5")))
        await record("hold", tag, "end")

    @app.task(
        name="flaky",
        queue=QUEUE,
        pass_context=True,
        retry=RetryStrategy(max_attempts=3, wait=0, retry_exceptions={TransientError}),
    )
    async def flaky(context: JobContext, kind: str, tag: str) -> None:
        await record("flaky", tag, f"attempt:{context.job.attempts}")
        if kind == "transient" and context.job.attempts < 2:  # fails twice, then succeeds
            raise TransientError(tag)
        if kind == "always_transient":
            raise TransientError(tag)
        if kind == "permanent":
            raise PermanentError(tag)
        await record("flaky", tag, "succeeded")

    @app.task(name="sleeper", queue=QUEUE)
    async def sleeper(tag: str) -> None:
        await record("sleeper", tag, "start")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await record("sleeper", tag, "cancelled")
            raise

    if periodic:

        @app.periodic(cron="* * * * * *", periodic_id="tick")  # every second
        @app.task(name="tick", queue=QUEUE)
        async def tick(timestamp: int) -> None:
            await record("tick", str(timestamp), "run")

    return app


async def run_worker(name: str) -> None:
    app = make_app(os.environ["SPIKE_DOMAIN_URL"], periodic=os.environ.get("SPIKE_PERIODIC") == "1")
    async with app.open_async():
        sys.stdout.write("worker ready\n")
        sys.stdout.flush()
        await app.run_worker_async(
            name=name,
            queues=[QUEUE],
            concurrency=int(os.environ.get("SPIKE_CONCURRENCY", "4")),
            update_heartbeat_interval=0.5,
            stalled_worker_timeout=2.0,
            abort_job_polling_interval=0.5,
        )


if __name__ == "__main__":
    os.environ["SPIKE_WORKER"] = sys.argv[1]
    asyncio.run(run_worker(sys.argv[1]))
