"""Foundation: users, sessions, login attempts, append-only event log.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "lab"
TS = sa.TIMESTAMP(timezone=True)


def upgrade(app_role: str) -> None:
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_login_at", TS),
        sa.CheckConstraint("role IN ('viewer', 'researcher', 'admin')", name="role"),
        sa.CheckConstraint("username ~ '^[a-z0-9][a-z0-9._-]{1,62}$'", name="username_format"),
        sa.UniqueConstraint("username"),
        schema=S,
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("ip", pg.INET),
        sa.Column("user_agent", sa.Text),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_index(
        "ix_auth_sessions_user_active", "auth_sessions", ["user_id", "expires_at"], schema=S
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("ip", pg.INET),
        sa.Column("succeeded", sa.Boolean, nullable=False),
        sa.Column("attempted_at", TS, nullable=False),
        schema=S,
    )
    op.create_index(
        "ix_login_attempts_username_time", "login_attempts", ["username", "attempted_at"], schema=S
    )
    op.create_index("ix_login_attempts_ip_time", "login_attempts", ["ip", "attempted_at"], schema=S)

    op.create_table(
        "events",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("type_version", sa.SmallInteger, nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("recorded_at", TS, nullable=False),
        sa.Column("actor_type", sa.Text, nullable=False),
        sa.Column("actor_id", sa.Text),
        sa.Column("aggregate_type", sa.Text),
        sa.Column("aggregate_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("causation_id", pg.UUID(as_uuid=True)),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("prev_hash", sa.LargeBinary, nullable=False),
        sa.Column("hash", sa.LargeBinary, nullable=False),
        sa.CheckConstraint("actor_type IN ('user', 'agent', 'system')", name="actor_type"),
        sa.CheckConstraint(
            "octet_length(hash) = 32 AND octet_length(prev_hash) = 32", name="hash_len"
        ),
        sa.UniqueConstraint("id"),
        schema=S,
    )
    op.create_index(
        "ix_events_aggregate", "events", ["aggregate_type", "aggregate_id", "seq"], schema=S
    )
    op.create_index("ix_events_type_seq", "events", ["type", "seq"], schema=S)
    op.create_index(
        "ix_events_correlation",
        "events",
        ["correlation_id"],
        schema=S,
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )

    # Append-only enforcement, independent of which role connects (owner included).
    op.execute(
        f"""
        CREATE FUNCTION {S}.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'table %.% is append-only (% rejected)',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        """
    )
    op.execute(
        f"CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON {S}.events "
        f"FOR EACH ROW EXECUTE FUNCTION {S}.forbid_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON {S}.events "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {S}.forbid_mutation()"
    )

    # Least-privilege grants for the application role (skipped if the role does not exist,
    # e.g. in a throwaway database; readiness/tests will then fail loudly on access).
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_role}') THEN
            GRANT USAGE ON SCHEMA {S} TO "{app_role}";
            GRANT SELECT ON {S}.alembic_version TO "{app_role}";
            GRANT SELECT, INSERT, UPDATE, DELETE
                ON {S}.users, {S}.auth_sessions, {S}.login_attempts TO "{app_role}";
            GRANT SELECT, INSERT ON {S}.events TO "{app_role}";
          ELSE
            RAISE WARNING 'application role % does not exist; no grants issued', '{app_role}';
          END IF;
        END $$;
        """
    )


def downgrade(app_role: str) -> None:
    op.drop_table("events", schema=S)  # drops its triggers
    op.execute(f"DROP FUNCTION {S}.forbid_mutation()")
    op.drop_table("login_attempts", schema=S)
    op.drop_table("auth_sessions", schema=S)
    op.drop_table("users", schema=S)
