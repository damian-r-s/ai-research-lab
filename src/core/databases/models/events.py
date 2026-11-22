"""ORM mapping of the append-only event log (immutability is enforced in the DB, see migration)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, LargeBinary, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.databases.base import Base

ACTOR_TYPES = ("user", "agent", "system")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(f"actor_type IN {ACTOR_TYPES!r}", name="actor_type"),
        CheckConstraint(
            "octet_length(hash) = 32 AND octet_length(prev_hash) = 32", name="hash_len"
        ),
        Index("ix_events_aggregate", "aggregate_type", "aggregate_id", "seq"),
        Index("ix_events_type_seq", "type", "seq"),
        Index(
            "ix_events_correlation", "correlation_id", postgresql_where="correlation_id IS NOT NULL"
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    type_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    aggregate_type: Mapped[str | None] = mapped_column(Text)
    aggregate_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
