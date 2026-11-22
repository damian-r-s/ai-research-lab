"""Runs, agent tasks, approvals, budgets, runners and the global kill switch (ARCHITECTURE §7.5).

This is the domain record of orchestration. LangGraph checkpoints (schema `langgraph`) and
dispatch jobs (schema `jobs`) are separate; nothing here parses them (ARCHITECTURE §7.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.databases.base import Base

RUN_STATUSES = (
    "queued",
    "running",
    "paused",
    "awaiting_approval",
    "cancel_requested",
    "stopped",
    "completed",
    "failed",
)
TASK_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled", "blocked_budget")
ATTEMPT_OUTCOMES = ("succeeded", "failed", "timed_out", "cancelled", "abandoned")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")
BUDGET_SCOPES = ("global_day", "global_month", "project", "run", "model", "role")
LEDGER_ENTRIES = ("reserve", "commit", "release")
RUNNER_STATUSES = ("active", "stopping", "stopped", "dead")

COST = Numeric(14, 6)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class SystemState(Base):
    """Singleton row. The kill switch lives in the database so it survives restarts."""

    __tablename__ = "system_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(nullable=False)


class Runner(Base):
    __tablename__ = "runners"
    __table_args__ = (CheckConstraint(_in("status", RUNNER_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(nullable=False)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (CheckConstraint(_in("scope_type", BUDGET_SCOPES), name="scope_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column()
    limit_tokens: Mapped[int | None] = mapped_column(BigInteger)
    limit_cost: Mapped[Decimal | None] = mapped_column(COST)
    limit_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    limit_attempts: Mapped[int | None] = mapped_column(Integer)
    limit_parallel: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class BudgetLedgerEntry(Base):
    """Append-only (the application role may only SELECT and INSERT)."""

    __tablename__ = "budget_ledger"
    __table_args__ = (
        CheckConstraint(_in("entry", LEDGER_ENTRIES), name="entry"),
        Index("ix_budget_ledger_budget_time", "budget_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budgets.id"), nullable=False
    )
    ref_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    entry: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost: Mapped[Decimal] = mapped_column(COST, nullable=False)
    seconds: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ResearchRun(Base):
    """One run = one LangGraph thread (1:1)."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        Index("ix_research_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # FK in Phase 4
    graph_key: Mapped[str] = mapped_column(Text, nullable=False)
    graph_version: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    pause_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column()
    stop_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    runner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runners.id", ondelete="SET NULL")
    )
    budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budgets.id")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column()
    # When this finished run's checkpoints were pruned to the latest one (criterion 2.11).
    checkpoints_pruned_at: Mapped[datetime | None] = mapped_column()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AgentTask(Base):
    """One logical unit of work per graph position (spec §4.2). Not a queue."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        Index("ix_agent_tasks_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    # {thread_id}:{node_path}:{fanout_key} (ARCHITECTURE §7.2)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    node_path: Mapped[str] = mapped_column(Text, nullable=False)
    role_key: Mapped[str | None] = mapped_column(Text)
    role_version_hash: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    completion_criteria: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result_refs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cost_external: Mapped[Decimal] = mapped_column(COST, nullable=False, server_default=text("0"))
    compute_seconds: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column()


class AgentTaskAttempt(Base):
    """Each execution of a task. An interrupted attempt is kept as `abandoned`, never erased."""

    __tablename__ = "agent_task_attempts"
    __table_args__ = (
        CheckConstraint(f"outcome IS NULL OR {_in('outcome', ATTEMPT_OUTCOMES)}", name="outcome"),
        UniqueConstraint("agent_task_id", "attempt_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    runner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runners.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column()
    outcome: Mapped[str | None] = mapped_column(Text)  # NULL while running
    error: Mapped[str | None] = mapped_column(Text)


class Approval(Base):
    """A human decision a run waits for. `request_key` makes the request an upsert (C-5)."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        Index("ix_approvals_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    request_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False)
