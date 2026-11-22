"""All ORM models. Importing this package makes `Base.metadata` complete (Alembic, tests)."""

from src.core.databases.base import Base
from src.core.databases.models.events import Event
from src.core.databases.models.users import AuthSession, LoginAttempt, User

metadata = Base.metadata

__all__ = ["AuthSession", "Base", "Event", "LoginAttempt", "User", "metadata"]
