from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from src.core.config import REPO_ROOT
from test.conftest import ScratchDatabase
from test.spike.support import SpikeDB, prepare


@pytest.fixture
def spike_db(make_database: Callable[[], ScratchDatabase]) -> SpikeDB:
    db = make_database()
    return prepare(db.name, db.app_url, db.owner_url, db.admin_url)


@pytest.fixture
async def checkpointer_ready(spike_db: SpikeDB) -> SpikeDB:
    """Create the checkpointer tables once (as the graph role, inside its own schema)."""
    from test.spike.graphs import open_saver

    async with open_saver(spike_db.graph_url) as saver:
        await saver.setup()
    return spike_db


class Processes:
    """Start spike runner/worker processes; kill anything left over at the end of a test."""

    def __init__(self, db: SpikeDB) -> None:
        self.db = db
        self.procs: list[subprocess.Popen[str]] = []

    def start(
        self, module: str, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.Popen[str]:
        proc = subprocess.Popen(  # noqa: S603 - fixed test modules
            [sys.executable, "-m", module, *args],
            cwd=REPO_ROOT,
            env={**os.environ, **self.db.env(), **(env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.procs.append(proc)
        return proc

    def run_graph(self, *args: str, timeout: float = 60) -> dict[str, Any]:
        proc = self.start("test.spike.graph_runner", *args)
        out, err = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, err
        result: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
        return result

    @staticmethod
    def kill9(proc: subprocess.Popen[str]) -> None:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        assert proc.returncode == -signal.SIGKILL

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)


@pytest.fixture
def procs(spike_db: SpikeDB) -> Iterator[Processes]:
    p = Processes(spike_db)
    yield p
    p.cleanup()
