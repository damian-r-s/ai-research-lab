"""Orchestration domain record: runs, agent tasks and attempts, approvals, budgets and their
ledger, runners, and the global kill switch (ARCHITECTURE §7.5, Phase 2).

Grants follow least privilege: no DELETE anywhere (the record is kept), the budget ledger is
append-only (SELECT, INSERT) and the kill switch row can only be read and updated.

Revision ID: 0003_runs
Revises: 0002_jobs_and_langgraph
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_runs"
down_revision: str | None = "0002_jobs_and_langgraph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(app_role: str, **_: str) -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("period_start", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("limit_tokens", sa.BigInteger(), nullable=True),
        sa.Column("limit_cost", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("limit_seconds", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("limit_attempts", sa.Integer(), nullable=True),
        sa.Column("limit_parallel", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('global_day', 'global_month', 'project', 'run', 'model', 'role')",
            name=op.f("ck_budgets_scope_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        schema="lab",
    )
    op.create_table(
        "runners",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("heartbeat_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'stopping', 'stopped', 'dead')", name=op.f("ck_runners_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runners")),
        schema="lab",
    )
    op.create_table(
        "system_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.Text(), nullable=True),
        sa.Column("changed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_system_state_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_state")),
        schema="lab",
    )
    op.create_table(
        "budget_ledger",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("budget_id", sa.UUID(), nullable=False),
        sa.Column("ref_type", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("entry", sa.Text(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("seconds", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entry IN ('reserve', 'commit', 'release')", name=op.f("ck_budget_ledger_entry")
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"], ["lab.budgets.id"], name=op.f("fk_budget_ledger_budget_id_budgets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_ledger")),
        schema="lab",
    )
    op.create_index(
        "ix_budget_ledger_budget_time",
        "budget_ledger",
        ["budget_id", "created_at"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "research_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("graph_key", sa.Text(), nullable=False),
        sa.Column("graph_version", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cancel_requested_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("runner_id", sa.UUID(), nullable=True),
        sa.Column("budget_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'awaiting_approval', 'cancel_requested', 'stopped', 'completed', 'failed')",
            name=op.f("ck_research_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"], ["lab.budgets.id"], name=op.f("fk_research_runs_budget_id_budgets")
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["lab.runners.id"],
            name=op.f("fk_research_runs_runner_id_runners"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_runs")),
        sa.UniqueConstraint("thread_id", name=op.f("uq_research_runs_thread_id")),
        schema="lab",
    )
    op.create_index(
        "ix_research_runs_status", "research_runs", ["status"], unique=False, schema="lab"
    )
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("node_path", sa.Text(), nullable=False),
        sa.Column("role_key", sa.Text(), nullable=True),
        sa.Column("role_version_hash", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("input_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completion_criteria", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_external",
            sa.Numeric(precision=14, scale=6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "compute_seconds",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'blocked_budget')",
            name=op.f("ck_agent_tasks_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.research_runs.id"],
            name=op.f("fk_agent_tasks_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tasks")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_agent_tasks_idempotency_key")),
        schema="lab",
    )
    op.create_index("ix_agent_tasks_run", "agent_tasks", ["run_id"], unique=False, schema="lab")
    op.create_table(
        "approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name=op.f("ck_approvals_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.research_runs.id"],
            name=op.f("fk_approvals_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        sa.UniqueConstraint("request_key", name=op.f("uq_approvals_request_key")),
        schema="lab",
    )
    op.create_index("ix_approvals_status", "approvals", ["status"], unique=False, schema="lab")
    op.create_table(
        "agent_task_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_task_id", sa.UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.UUID(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'abandoned')",
            name=op.f("ck_agent_task_attempts_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_task_id"],
            ["lab.agent_tasks.id"],
            name=op.f("fk_agent_task_attempts_agent_task_id_agent_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["lab.runners.id"],
            name=op.f("fk_agent_task_attempts_runner_id_runners"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_task_attempts")),
        sa.UniqueConstraint(
            "agent_task_id",
            "attempt_no",
            name=op.f("uq_agent_task_attempts_agent_task_id_attempt_no"),
        ),
        schema="lab",
    )
    op.execute(
        "INSERT INTO lab.system_state (id, kill_switch, reason, changed_by, changed_at) "
        "VALUES (1, false, NULL, 'migration', now())"
    )
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE ON lab.runners, lab.budgets, lab.research_runs,
                lab.agent_tasks, lab.agent_task_attempts, lab.approvals TO "{app_role}";
            GRANT SELECT, INSERT ON lab.budget_ledger TO "{app_role}";
            GRANT SELECT, UPDATE ON lab.system_state TO "{app_role}";
          ELSE
            RAISE WARNING 'application role % does not exist; no grants issued', '{app_role}';
          END IF;
        END $$;
        """
    )


def downgrade(app_role: str, **_: str) -> None:
    op.drop_table("agent_task_attempts", schema="lab")
    op.drop_index("ix_approvals_status", table_name="approvals", schema="lab")
    op.drop_table("approvals", schema="lab")
    op.drop_index("ix_agent_tasks_run", table_name="agent_tasks", schema="lab")
    op.drop_table("agent_tasks", schema="lab")
    op.drop_index("ix_research_runs_status", table_name="research_runs", schema="lab")
    op.drop_table("research_runs", schema="lab")
    op.drop_index("ix_budget_ledger_budget_time", table_name="budget_ledger", schema="lab")
    op.drop_table("budget_ledger", schema="lab")
    op.drop_table("system_state", schema="lab")
    op.drop_table("runners", schema="lab")
    op.drop_table("budgets", schema="lab")
