"""Declarative base. All tables live in one dedicated schema (default ``lab``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase

# Stable constraint names keep Alembic migrations deterministic and reviewable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# All lab tables live in this schema. LangGraph checkpoints and dispatch jobs get their own
# schemas (Phase 2), so the three persistence concerns stay separate (ARCHITECTURE §7.1).
SCHEMA = "lab"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: TIMESTAMP(timezone=True)}  # noqa: RUF012
