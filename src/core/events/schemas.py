"""Public representation of an audit event (API responses and the SSE stream)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.core.databases.models.events import Event


class EventOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    id: uuid.UUID
    type: str
    type_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_type: str
    actor_id: str | None
    aggregate_type: str | None
    aggregate_id: str | None
    correlation_id: str | None
    causation_id: uuid.UUID | None
    payload: dict[str, Any]
    hash: str  # hex SHA-256 chain hash, so clients can cross-check `verify_events` output

    @classmethod
    def from_row(cls, row: Event) -> EventOut:
        return cls(
            seq=row.seq,
            id=row.id,
            type=row.type,
            type_version=row.type_version,
            occurred_at=row.occurred_at,
            recorded_at=row.recorded_at,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            payload=row.payload,
            hash=row.hash.hex(),
        )
