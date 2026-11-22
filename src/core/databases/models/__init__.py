"""All ORM models. Importing this package makes `Base.metadata` complete (Alembic, tests)."""

from src.core.databases.base import Base
from src.core.databases.models.events import Event
from src.core.databases.models.runs import (
    AgentTask,
    AgentTaskAttempt,
    Approval,
    Budget,
    BudgetLedgerEntry,
    ResearchRun,
    Runner,
    SystemState,
)
from src.core.databases.models.users import AuthSession, LoginAttempt, User

metadata = Base.metadata

__all__ = [
    "AgentTask",
    "AgentTaskAttempt",
    "Approval",
    "AuthSession",
    "Base",
    "Budget",
    "BudgetLedgerEntry",
    "Event",
    "LoginAttempt",
    "ResearchRun",
    "Runner",
    "SystemState",
    "User",
    "metadata",
]
