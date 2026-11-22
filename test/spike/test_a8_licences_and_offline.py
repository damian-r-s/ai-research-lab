"""A-8: licences of the whole dependency closure permit self-hosted private use, and the OSS
LangGraph library makes no call to hosted services (LangSmith is installed transitively)."""

from __future__ import annotations

import importlib.metadata as md
import ipaddress
import re
import socket
from typing import Any

import pytest
from packaging.requirements import Requirement

from test.spike.graphs import Ctx, build, config, initial, open_pool, open_saver
from test.spike.support import SpikeDB

pytestmark = pytest.mark.spike

ROOTS = ["langgraph", "langgraph-checkpoint-postgres", "procrastinate"]
# Permissive, or weak copyleft that only applies to modifications of the library itself.
ALLOWED = re.compile(
    r"^(MIT|BSD-2-Clause|BSD-3-Clause|Apache-2\.0|Apache 2\.0|Apache License, Version 2\.0|PSF-2\.0"
    r"|ISC|MPL-2\.0|LGPL-3\.0-only|LGPL-3\.0-or-later)$"
)
STRONG_COPYLEFT = re.compile(r"\b(A?GPL|SSPL|BUSL|Commons Clause|Elastic)\b", re.IGNORECASE)


def closure(roots: list[str]) -> dict[str, md.Distribution]:
    seen: dict[str, md.Distribution] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        dist = md.distribution(name)
        seen[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                stack.append(req.name)
    return seen


def licence_terms(dist: md.Distribution) -> list[str]:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return [t.strip("() ") for t in re.split(r"\s+(?:AND|OR)\s+", expression)]
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    mapping = {
        "MIT License": "MIT",
        "Apache Software License": "Apache-2.0",
        "BSD License": "BSD-3-Clause",
        "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    }
    terms = [mapping.get(c.split("::")[-1].strip(), c.split("::")[-1].strip()) for c in classifiers]
    return terms or [(meta.get("License") or "UNKNOWN").strip()]


def test_a8_every_dependency_has_an_allowed_licence() -> None:
    deps = closure(ROOTS)
    assert {"langgraph", "langgraph-checkpoint-postgres", "procrastinate", "langsmith"} <= set(deps)
    problems = {}
    for key, dist in deps.items():
        terms = licence_terms(dist)
        if not any(ALLOWED.match(t) for t in terms) or any(
            STRONG_COPYLEFT.search(t) and not t.startswith("LGPL") for t in terms
        ):
            problems[key] = terms
    assert problems == {}


@pytest.mark.integration
async def test_a8_graph_runs_make_no_outbound_network_connection(
    checkpointer_ready: SpikeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default configuration (no LANGSMITH_* / LANGCHAIN_* variables): every socket the run opens
    is the local database socket. Any TCP/UDP connect to a non-loopback address fails the test."""
    for var in (
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    attempts: list[Any] = []
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0]
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
            if not local:
                attempts.append(address)
                raise OSError("outbound connection blocked by test")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, a: guarded(self, a) or 0)

    db, thread = checkpointer_ready, "offline"
    async with open_saver(db.graph_url) as saver, open_pool(db.domain_url) as pool:
        graph = build("fanout").compile(checkpointer=saver)
        await graph.ainvoke(
            initial(thread),
            config(thread),
            context=Ctx(pool=pool, dsn=db.domain_url),
        )
        graph = build("approval").compile(checkpointer=saver)
        await graph.ainvoke(
            initial("offline-2"),
            config("offline-2"),
            context=Ctx(pool=pool, dsn=db.domain_url),
        )
    assert attempts == []
