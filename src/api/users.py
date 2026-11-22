"""User administration (admin only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.api.deps import DbSession, request_id, require_role
from src.api.schemas import UserCreateRequest, UserOut
from src.core.auth import service
from src.core.auth.service import Principal
from src.core.databases.repositories import users_repo
from src.core.events.store import EventContext
from src.core.security import Role

router = APIRouter(prefix="/api/users", tags=["users"])
Admin = Annotated[Principal, Depends(require_role(Role.admin))]


@router.get("", response_model=list[UserOut])
async def list_users(_: Admin, session: DbSession) -> list[UserOut]:
    return [UserOut.from_row(u) for u in await users_repo.list_users(session)]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreateRequest, request: Request, admin: Admin, session: DbSession
) -> UserOut:
    user = await service.create_user(
        session,
        username=body.username,
        password=body.password,
        role=body.role,
        context=EventContext(admin.actor, correlation_id=request_id(request)),
    )
    await session.commit()
    return UserOut.from_row(user)
