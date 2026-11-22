"""`lab.selftest`: a deterministic graph that exercises the whole runtime without any model.

Used by the tests and on staging/production to check orchestration end to end: sequential steps,
a parallel fan-out, a bounded review/rework loop, an optional human approval, pausing, stopping
and crash recovery. Every node is a recorded agent task. No LLM, no network.
"""

from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field

from src.agents import registry
from src.agents.context import RunContext
from src.agents.nodes import ask_approval, bounded, task

KEY, VERSION = "lab.selftest", "1"
MAX_REWORK_CYCLES = 2  # the configured loop bound (ARCHITECTURE §6.2); tests use review_rejections


class SelftestParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: int = Field(default=3, ge=1, le=20)
    fanout: int = Field(default=0, ge=0, le=8)
    delay_s: float = Field(default=0.0, ge=0.0, le=60.0)  # simulated work per node
    review_rejections: int = Field(
        default=0, ge=0, le=100
    )  # how often the reviewer asks for rework
    approval: bool = False
    fail_at_step: int | None = Field(default=None, ge=1)  # deterministic failure, for tests


class State(TypedDict, total=False):
    params: dict[str, Any]
    steps_done: int
    probes: Annotated[list[int], operator.add]
    rework_cycles: int
    review_passed: bool
    approved: bool | None
    log: Annotated[list[str], operator.add]


class ProbeInput(TypedDict):
    params: dict[str, Any]
    i: int


def _p(state: State | ProbeInput) -> SelftestParams:
    return SelftestParams.model_validate(state["params"])


async def _work(p: SelftestParams, result: dict[str, Any]) -> dict[str, Any]:
    if p.delay_s:
        await asyncio.sleep(p.delay_s)
    return result


async def plan(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p = _p(state)
    await task(runtime.context, "plan", lambda: _work(p, {"planned": p.steps}))
    return {"log": ["plan"], "steps_done": 0, "rework_cycles": 0}


def after_plan(state: State) -> list[Send] | str:
    p = _p(state)
    if p.fanout:
        return [Send("probe", {"params": state["params"], "i": i}) for i in range(p.fanout)]
    return "step"


async def probe(inp: ProbeInput, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p, i = _p(inp), inp["i"]
    await task(runtime.context, "probe", lambda: _work(p, {"probe": i}), fanout_key=str(i))
    return {"probes": [i]}


async def step(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p, n = _p(state), state.get("steps_done", 0) + 1

    async def work() -> dict[str, Any]:
        if p.fail_at_step == n:
            raise RuntimeError(f"selftest failure requested at step {n}")
        return await _work(p, {"step": n})

    await task(runtime.context, "step", work, fanout_key=str(n))
    return {"steps_done": n, "log": [f"step {n}"]}


def after_step(state: State) -> str:
    return "step" if state["steps_done"] < _p(state).steps else "review"


async def review(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p, cycle = _p(state), state.get("rework_cycles", 0)
    passed = cycle >= p.review_rejections
    await task(
        runtime.context, "review", lambda: _work(p, {"passed": passed}), fanout_key=str(cycle)
    )
    return {"review_passed": passed, "log": [f"review {cycle}: {'pass' if passed else 'rework'}"]}


def after_review(state: State) -> str:
    if state["review_passed"]:
        return "approve" if _p(state).approval else "finish"
    return bounded(state["rework_cycles"], MAX_REWORK_CYCLES, again="rework", done="finish")


async def rework(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p, cycle = _p(state), state["rework_cycles"] + 1
    await task(runtime.context, "rework", lambda: _work(p, {"cycle": cycle}), fanout_key=str(cycle))
    return {"rework_cycles": cycle, "log": [f"rework {cycle}"]}


async def approve(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    answer = await ask_approval(
        runtime.context,
        "approve",
        kind="selftest_gate",
        payload={"question": "Continue the self-test run?", "steps_done": state["steps_done"]},
    )
    return {
        "approved": bool(answer.get("approved")),
        "log": [f"approval: {answer.get('approved')}"],
    }


async def finish(state: State, runtime: Runtime[RunContext]) -> dict[str, Any]:
    p = _p(state)
    outcome = "rework limit reached" if not state.get("review_passed") else "done"
    await task(runtime.context, "finish", lambda: _work(p, {"outcome": outcome}))
    return {"log": [f"finish: {outcome}"]}


def build() -> StateGraph[State, RunContext, Any, Any]:
    g: StateGraph[State, RunContext, Any, Any] = StateGraph(State, context_schema=RunContext)
    g.add_node("plan", plan)
    # Receives a Send payload, not State; mypy cannot infer the overload for a custom input schema.
    g.add_node("probe", probe, input_schema=ProbeInput)  # type: ignore[call-overload]
    g.add_node("step", step)
    g.add_node("review", review)
    g.add_node("rework", rework)
    g.add_node("approve", approve)
    g.add_node("finish", finish)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", after_plan, ["probe", "step"])
    g.add_edge("probe", "step")
    g.add_conditional_edges("step", after_step, ["step", "review"])
    g.add_conditional_edges("review", after_review, ["rework", "approve", "finish"])
    g.add_edge("rework", "review")
    g.add_edge("approve", "finish")
    g.add_edge("finish", END)
    return g


def initial_state(params: BaseModel) -> dict[str, Any]:
    return {"params": params.model_dump()}


SELFTEST = registry.register(
    registry.GraphDefinition(
        key=KEY,
        version=VERSION,
        description="Deterministic orchestration self-test (no models)",
        params_model=SelftestParams,
        build=build,
        initial_state=initial_state,
    )
)
