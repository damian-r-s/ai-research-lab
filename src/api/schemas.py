"""Request and response bodies shared by the API routers."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.core.databases.models.users import User
from src.core.security import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class MeResponse(BaseModel):
    username: str
    role: Role


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=2, max_length=63)
    password: str = Field(min_length=1, max_length=1024)
    role: Role


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_row(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            username=user.username,
            role=Role(user.role),
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
