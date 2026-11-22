"""Registered graphs. A run names its graph by key and version; the executor builds it from here.

Registration rejects any graph with a synchronous node (DECISIONS C-2): LangGraph runs sync nodes
in a worker thread, which a stop cannot interrupt (spike A-5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph._internal._runnable import RunnableCallable
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from src.agents.context import RunContext
from src.core.errors import NotFound


class SyncNodeError(TypeError):
    pass


@dataclass(frozen=True, slots=True)
class GraphDefinition:
    key: str
    version: str
    description: str
    params_model: type[BaseModel]
    build: Callable[[], StateGraph[Any, RunContext, Any, Any]]
    initial_state: Callable[[BaseModel], dict[str, Any]]


_GRAPHS: dict[tuple[str, str], GraphDefinition] = {}


def assert_async_nodes(builder: StateGraph[Any, Any, Any, Any], *, path: str = "") -> None:
    for name, spec in builder.nodes.items():
        runnable = spec.runnable
        where = f"{path}{name}"
        if isinstance(runnable, RunnableCallable):
            if runnable.func is not None:  # sync callables keep `func`; async ones only `afunc`
                raise SyncNodeError(f"node {where!r} is synchronous; graph nodes must be async")
        elif isinstance(runnable, CompiledStateGraph):
            assert_async_nodes(runnable.builder, path=f"{where}/")  # subgraph
        else:
            raise SyncNodeError(f"node {where!r} has an unsupported type {type(runnable).__name__}")


def register(definition: GraphDefinition) -> GraphDefinition:
    assert_async_nodes(definition.build())
    key = (definition.key, definition.version)
    if key in _GRAPHS and _GRAPHS[key] is not definition:
        raise ValueError(f"graph {definition.key} v{definition.version} is already registered")
    _GRAPHS[key] = definition
    return definition


def get(key: str, version: str) -> GraphDefinition:
    try:
        return _GRAPHS[(key, version)]
    except KeyError:
        raise NotFound(f"graph {key} v{version} is not registered") from None


def all_graphs() -> list[GraphDefinition]:
    return sorted(_GRAPHS.values(), key=lambda d: (d.key, d.version))
