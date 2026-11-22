"""Database access for users, sessions and login attempts. No business rules here."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.databases.models.users import AuthSession, LoginAttempt, User
from src.core.ids import uuid7


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username))


async def list_users(session: AsyncSession) -> list[User]:
    return list((await session.scalars(select(User).order_by(User.username))).all())


async def count_recent_failures(
    session: AsyncSession, *, username: str, ip: str | None, since: datetime
) -> int:
    """Failed logins since `since` for this username OR from this IP address."""
    matches = [LoginAttempt.username == username]
    if ip:
        matches.append(LoginAttempt.ip == ip)
    stmt = select(func.count()).where(
        LoginAttempt.succeeded.is_(False), LoginAttempt.attempted_at >= since, or_(*matches)
    )
    return int(await session.scalar(stmt) or 0)


def add_login_attempt(
    session: AsyncSession, *, username: str, ip: str | None, succeeded: bool, at: datetime
) -> None:
    session.add(
        LoginAttempt(id=uuid7(), username=username, ip=ip, succeeded=succeeded, attempted_at=at)
    )


async def get_session_with_user(
    session: AsyncSession, token_hash: str
) -> tuple[AuthSession, User] | None:
    row = (
        await session.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == token_hash)
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def touch_session(session: AsyncSession, session_id: uuid.UUID, at: datetime) -> None:
    await session.execute(
        update(AuthSession).where(AuthSession.id == session_id).values(last_seen_at=at)
    )


async def revoke_session(session: AsyncSession, session_id: uuid.UUID, at: datetime) -> bool:
    """Returns False if the session was already revoked."""
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=at)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
