"""Tamper-evident hash chain over the event log.

hash_n = SHA-256( prev_hash || canonical_json(event_n) ), with prev_hash of the first event being
32 zero bytes. Changing, removing or reordering any committed event breaks every later link.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

GENESIS_HASH = bytes(32)


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def canonical_bytes(
    *,
    event_id: UUID,
    type_: str,
    type_version: int,
    occurred_at: datetime,
    recorded_at: datetime,
    actor_type: str,
    actor_id: str | None,
    aggregate_type: str | None,
    aggregate_id: str | None,
    correlation_id: str | None,
    causation_id: UUID | None,
    payload: dict[str, Any],
) -> bytes:
    doc = {
        "id": str(event_id),
        "type": type_,
        "type_version": type_version,
        "occurred_at": _ts(occurred_at),
        "recorded_at": _ts(recorded_at),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "correlation_id": correlation_id,
        "causation_id": str(causation_id) if causation_id else None,
        "payload": payload,
    }
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def chain_hash(prev_hash: bytes, canonical: bytes) -> bytes:
    if len(prev_hash) != 32:
        raise ValueError("prev_hash must be 32 bytes")
    return hashlib.sha256(prev_hash + canonical).digest()
