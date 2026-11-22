"""A-1 to A-4: crash durability, idempotent re-execution, parallel branches, interrupts.

Every crash is a real `kill -9` of a separate runner process, and every resume runs in a fresh
process, the same situation as a runner pod dying on the server.
"""

from __future__ import annotations

import pytest

from test.spike.conftest import Processes
from test.spike.graphs import Ctx, build, config, initial, open_pool, open_saver
from test.spike.support import SpikeDB, count, effects, query, wait_until

pytestmark = [pytest.mark.integration, pytest.mark.spike]

HANGING = "SELECT count(*) FROM spike.effects WHERE thread = %s AND node = %s AND event = 'hanging'"


def checkpoint_count(db: SpikeDB, thread: str) -> int:
    return int(
        query(
            db.admin_url,
            "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s",
            (thread,),
        )[0][0]
    )


# --- A-1 ------------------------------------------------------------------------------------


def test_a1_thread_resumes_after_kill9_from_last_checkpoint(
    checkpointer_ready: SpikeDB, procs: Processes
) -> None:
    db, thread = checkpointer_ready, "a1"
    runner = procs.start("test.spike.graph_runner", "linear", thread, "--start", "--hang", "d")
    wait_until(db.owner_url, HANGING, (thread, "d"))
    procs.kill9(runner)
    assert checkpoint_count(db, thread) > 0  # state survived the process

    result = procs.run_graph("linear", thread)  # fresh process, no input: continue
    assert result["values"]["steps"] == ["a", "b", "agent-answer", "d"]
    assert result["next"] == []
    assert result["pid"] != runner.pid
    # Completed super-steps were not repeated; only the node that was running re-executed.
    assert count(db, thread, "a", "start") == 1
    assert count(db, thread, "b", "start") == 1
    assert count(db, thread, "agent", "start") == 1
    assert count(db, thread, "d", "start") == 2


# --- A-2 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("crash_point", ["agent:before_commit", "agent:after_commit"])
def test_a2_node_reexecutes_but_idempotency_prevents_duplicate_work(
    checkpointer_ready: SpikeDB, procs: Processes, crash_point: str
) -> None:
    db, thread = checkpointer_ready, f"a2-{crash_point.split(':')[1]}"
    runner = procs.start(
        "test.spike.graph_runner", "linear", thread, "--start", "--hang", crash_point
    )
    wait_until(db.owner_url, HANGING, (thread, crash_point))
    procs.kill9(runner)

    result = procs.run_graph("linear", thread)
    assert result["values"]["steps"] == ["a", "b", "agent-answer", "d"]
    # LangGraph re-executed the node from its start (ARCHITECTURE §6.7) ...
    assert count(db, thread, "agent", "start") == 2
    # ... but the model was called once, and there is one logical task.
    assert query(db.owner_url, "SELECT count(*) FROM spike.model_calls")[0][0] == 1
    assert query(db.owner_url, "SELECT key, status FROM spike.tasks") == [
        (f"{thread}:agent", "succeeded")
    ]
    attempts = [s for (s,) in query(db.owner_url, "SELECT status FROM spike.attempts ORDER BY id")]
    if crash_point == "agent:before_commit":
        assert count(db, thread, "agent", "model_cache_hit") == 1
        assert attempts == ["abandoned", "succeeded"]  # the interrupted attempt is kept, not erased
    else:
        assert count(db, thread, "agent", "reused_result") == 1
        assert attempts == ["succeeded"]  # the task had finished: nothing to redo


# --- A-3 ------------------------------------------------------------------------------------


async def test_a3_completed_branches_not_rerun_when_a_sibling_fails(
    checkpointer_ready: SpikeDB,
) -> None:
    db, thread = checkpointer_ready, "a3-fail"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("fanout").compile(checkpointer=saver)
        # branch3 fails 0.5 s after its siblings have finished (see graphs.branch).
        ctx = Ctx(pool=pool, dsn=db.domain_url, fail_at=frozenset({"branch3"}), delay=0.1)
        with pytest.raises(RuntimeError, match="branch3"):
            await graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        await graph.ainvoke(
            None, config(thread), context=Ctx(pool=pool, dsn=db.domain_url), durability="sync"
        )
        snap = await graph.aget_state(config(thread))
    assert sorted(snap.values["results"]) == [0, 1, 2, 3, 4]
    for i in (0, 1, 2, 4):
        assert count(db, thread, f"branch{i}", "start") == 1, effects(db, thread)
    assert count(db, thread, "branch3", "start") == 2
    assert count(db, thread, "join", "start") == 1


async def test_a3_fast_failure_cancels_running_siblings_and_only_they_rerun(
    checkpointer_ready: SpikeDB,
) -> None:
    """Finding: a failure cancels siblings still running in that super-step. Those (and only
    those) run again on resume; siblings that had finished are kept."""
    db, thread = checkpointer_ready, "a3-fastfail"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("fanout").compile(checkpointer=saver)
        ctx = Ctx(
            pool=pool,
            dsn=db.domain_url,
            delay=1.0,
            hang_at=frozenset(),
            extra={"fail_fast": {"branch0"}},
        )
        with pytest.raises(RuntimeError, match="branch0"):
            await graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        await graph.ainvoke(
            None, config(thread), context=Ctx(pool=pool, dsn=db.domain_url), durability="sync"
        )
        snap = await graph.aget_state(config(thread))
    assert sorted(snap.values["results"]) == [0, 1, 2, 3, 4]
    for i in range(1, 5):
        assert count(db, thread, f"branch{i}", "done") == 1, effects(db, thread)  # never twice
        assert count(db, thread, f"branch{i}", "start") == 2  # cancelled once, then re-run


def test_a3_completed_branches_not_rerun_after_kill9(
    checkpointer_ready: SpikeDB, procs: Processes
) -> None:
    db, thread = checkpointer_ready, "a3-kill"
    runner = procs.start(
        "test.spike.graph_runner", "fanout", thread, "--start", "--hang", "branch3"
    )
    wait_until(db.owner_url, HANGING, (thread, "branch3"))
    wait_until(
        db.owner_url,
        "SELECT count(*) = 4 FROM spike.effects WHERE thread = %s AND event = 'done'",
        (thread,),
    )
    # The finished branches' writes must be durable before the crash for this to mean anything.
    wait_until(
        db.admin_url,
        "SELECT count(DISTINCT task_id) >= 4 FROM langgraph.checkpoint_writes WHERE thread_id = %s",
        (thread,),
    )
    procs.kill9(runner)

    result = procs.run_graph("fanout", thread)
    assert sorted(result["values"]["results"]) == [0, 1, 2, 3, 4]
    for i in (0, 1, 2, 4):
        assert count(db, thread, f"branch{i}", "start") == 1, effects(db, thread)
    assert count(db, thread, "branch3", "start") == 2


# --- A-4 ------------------------------------------------------------------------------------


def test_a4_interrupt_survives_restart_and_resumes_in_another_process(
    checkpointer_ready: SpikeDB, procs: Processes
) -> None:
    db, thread = checkpointer_ready, "a4"
    first = procs.run_graph("approval", thread, "--start")
    assert first["next"] == ["gate"]
    assert first["interrupts"] == [{"question": "approve the next stage?", "thread": thread}]
    # The first process has exited: nothing holds the run while it waits (no runner occupied).

    second = procs.run_graph("approval", thread, "--resume", "approved")
    assert second["pid"] != first["pid"]
    assert second["values"]["steps"] == ["a", "gate:approved", "d"]
    assert second["next"] == []
    # Documented LangGraph behaviour: the interrupted node restarts from its beginning on resume,
    # so code before interrupt() runs twice. Approval gates must therefore be side-effect free
    # before interrupt(), or idempotent (2b: the approvals row is an upsert keyed by task).
    assert count(db, thread, "gate", "start") == 2
    assert count(db, thread, "a", "start") == 1


def test_a4_interrupted_thread_survives_kill9_while_waiting(
    checkpointer_ready: SpikeDB, procs: Processes
) -> None:
    """A runner that dies right after the interrupt (before anyone answers) loses nothing."""
    thread = "a4-kill"
    runner = procs.start("test.spike.graph_runner", "approval", thread, "--start", "--linger")
    assert runner.stdout is not None and runner.stdout.readline() == "lingering\n"
    procs.kill9(runner)
    result = procs.run_graph("approval", thread, "--resume", "rejected")
    assert result["values"]["steps"] == ["a", "gate:rejected", "d"]
