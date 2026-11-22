"""A separate runner process for crash and hand-over tests (`kill -9` needs a real process).

    python -m test.spike.graph_runner <graph> <thread> [--start] [--resume VALUE]
                                      [--hang POINT ...] [--fail POINT ...] [--linger]

Prints one JSON line: the final values, the next nodes and any pending interrupts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from langgraph.types import Command

from test.spike.graphs import Ctx, build, config, initial, open_pool, open_saver


async def main(args: argparse.Namespace) -> dict[str, Any]:
    graph_url, domain_url = os.environ["SPIKE_GRAPH_URL"], os.environ["SPIKE_DOMAIN_URL"]
    async with open_saver(graph_url) as saver, open_pool(domain_url) as pool:
        graph = build(args.graph).compile(checkpointer=saver)
        ctx = Ctx(
            pool=pool, dsn=domain_url, hang_at=frozenset(args.hang), fail_at=frozenset(args.fail)
        )
        payload: Any
        if args.start:
            payload = initial(args.thread)
        elif args.resume is not None:
            payload = Command(resume=args.resume)
        else:
            payload = None  # continue from the last checkpoint
        await graph.ainvoke(payload, config(args.thread), context=ctx, durability="sync")
        snap = await graph.aget_state(config(args.thread))
        if args.linger:  # stay alive after the run (to be killed while "idle")
            sys.stdout.write("lingering\n")
            sys.stdout.flush()
            await asyncio.sleep(3600)
        return {
            "values": snap.values,
            "next": list(snap.next),
            "interrupts": [i.value for t in snap.tasks for i in t.interrupts],
            "pid": os.getpid(),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("graph")
    parser.add_argument("thread")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument("--hang", nargs="*", default=[])
    parser.add_argument("--fail", nargs="*", default=[])
    parser.add_argument("--linger", action="store_true")
    out = asyncio.run(main(parser.parse_args()))
    sys.stdout.write(json.dumps(out) + "\n")
