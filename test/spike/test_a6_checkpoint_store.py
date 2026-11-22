"""A-6: the checkpointer lives in its own schema under its own role, and supports pruning that
does not touch the domain record (criterion 2.11)."""

from __future__ import annotations

import psycopg
import pytest
from langgraph.types import Command

from test.spike.graphs import Ctx, build, config, initial, open_pool, open_saver
from test.spike.support import SpikeDB, query

pytestmark = [pytest.mark.integration, pytest.mark.spike]


def test_a6_tables_are_only_in_the_langgraph_schema(checkpointer_ready: SpikeDB) -> None:
    rows = query(
        checkpointer_ready.admin_url,
        "SELECT table_schema, table_name, tableowner FROM information_schema.tables "
        "JOIN pg_tables ON schemaname = table_schema AND tablename = table_name "
        "WHERE table_name LIKE 'checkpoint%%' ORDER BY 2",
    )
    assert {r[0] for r in rows} == {"langgraph"}
    assert {r[2] for r in rows} == {"lab_graph"}
    assert {r[1] for r in rows} >= {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}


def test_a6_graph_role_is_confined_to_its_schema(checkpointer_ready: SpikeDB) -> None:
    db = checkpointer_ready
    checks = query(
        db.admin_url,
        "SELECT has_schema_privilege('lab_graph', 'spike', 'USAGE'),"
        "       has_schema_privilege('lab_graph', 'jobs', 'USAGE'),"
        "       has_schema_privilege('lab_graph', 'public', 'CREATE'),"
        "       has_database_privilege('lab_graph', current_database(), 'CREATE'),"
        "       has_schema_privilege('lab_app', 'langgraph', 'USAGE')",
    )[0]
    assert checks == (False, False, False, False, False)
    with psycopg.connect(db.graph_url) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM spike.tasks")


async def test_a6_setup_is_idempotent(checkpointer_ready: SpikeDB) -> None:
    async with open_saver(checkpointer_ready.graph_url) as saver:
        await saver.setup()  # second run: no error, no duplicate migrations
    versions = query(
        checkpointer_ready.admin_url,
        "SELECT count(*), count(DISTINCT v) FROM langgraph.checkpoint_migrations",
    )
    assert versions[0][0] == versions[0][1]


def checkpoints(db: SpikeDB, thread: str) -> int:
    return int(
        query(
            db.admin_url,
            "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s",
            (thread,),
        )[0][0]
    )


async def test_a6_prune_keeps_latest_and_never_touches_the_domain(
    checkpointer_ready: SpikeDB,
) -> None:
    db = checkpointer_ready
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("linear").compile(checkpointer=saver)
        ctx = Ctx(pool=pool, dsn=db.domain_url)
        for thread in ("keep", "drop"):
            await graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        domain_before = query(db.owner_url, "SELECT * FROM spike.tasks ORDER BY key")
        assert checkpoints(db, "keep") > 1

        await saver.aprune(["keep"], strategy="keep_latest")
        await saver.aprune(["drop"], strategy="delete")

        assert checkpoints(db, "keep") == 1
        assert (await graph.aget_state(config("keep"))).values["steps"] == [
            "a",
            "b",
            "agent-answer",
            "d",
        ]
        assert checkpoints(db, "drop") == 0
        for table in ("checkpoint_blobs", "checkpoint_writes"):
            assert (
                query(
                    db.admin_url,
                    f"SELECT count(*) FROM langgraph.{table} WHERE thread_id = 'drop'",  # noqa: S608
                )[0][0]
                == 0
            )
    assert query(db.owner_url, "SELECT * FROM spike.tasks ORDER BY key") == domain_before


async def test_a6_prune_of_an_interrupted_thread_keeps_it_resumable(
    checkpointer_ready: SpikeDB,
) -> None:
    db, thread = checkpointer_ready, "prune-waiting"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("approval").compile(checkpointer=saver)
        ctx = Ctx(pool=pool, dsn=db.domain_url)
        await graph.ainvoke(initial(thread), config(thread), context=ctx, durability="sync")
        await saver.aprune([thread], strategy="keep_latest")
        assert checkpoints(db, thread) == 1
        await graph.ainvoke(
            Command(resume="approved"), config(thread), context=ctx, durability="sync"
        )
        final = await graph.aget_state(config(thread))
    assert final.values["steps"] == ["a", "gate:approved", "d"]
