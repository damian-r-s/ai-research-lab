"""Authentication use cases. Callers own the transaction; every state change emits an event."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.core.auth import events as auth_events
from src.core.clock import utcnow
from src.core.config import AuthSettings
from src.core.databases.models.users import AuthSession, LoginAttempt, Role, User
from src.core.errors import ConflictError, NotAuthenticated, TooManyAttempts
from src.core.events import store
from src.core.events.store import Actor, EventContext
from src.core.ids import uuid7
from src.core.security import hash_password, needs_rehash, verify_password

# Sessions are touched at most this often, to avoid a write on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_username(username: str) -> str:
    return username.strip().lower()


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session: AuthSession
    token: str  # returned once, to be set as a cookie; never persisted


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of a request."""

    user_id: uuid.UUID
    username: str
    role: Role
    session_id: uuid.UUID

    @property
    def actor(self) -> Actor:
        return Actor("user", str(self.user_id))


async def create_user(
    session: AsyncSession, *, username: str, password: str, role: Role, context: EventContext
) -> User:
    username = normalise_username(username)
    password_hash = await run_in_threadpool(hash_password, password)
    now = utcnow()
    user = User(
        id=uuid7(),
        username=username,
        password_hash=password_hash,
        role=role.value,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(f"user {username!r} already exists or is invalid") from exc
    await store.append(
        session,
        type_=auth_events.USER_CREATED.name,
        payload=auth_events.UserCreated(username=username, role=role.value),
        context=context,
        aggregate_type="user",
        aggregate_id=str(user.id),
    )
    return user


async def _recent_failures(
    session: AsyncSession, username: str, ip: str | None, since: datetime
) -> int:
    conds = [LoginAttempt.username == username]
    if ip:
        conds.append(LoginAttempt.ip == ip)
    stmt = select(func.count()).where(
        LoginAttempt.succeeded.is_(False), LoginAttempt.attempted_at >= since, or_(*conds)
    )
    return int(await session.scalar(stmt) or 0)


async def login(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
    settings: AuthSettings,
    correlation_id: str | None,
) -> LoginResult:
    """Verify credentials and open a session.

    On failure the attempt and a ``auth.login.failed`` event are flushed in the session and an
    exception is raised; the caller must still COMMIT so the failure is durably recorded
    (that is what makes throttling survive restarts).
    """
    username = normalise_username(username)
    now = utcnow()
    ctx = EventContext(Actor("user", None), correlation_id=correlation_id)

    async def fail(reason: str, actor_id: str | None = None) -> None:
        session.add(
            LoginAttempt(id=uuid7(), username=username, ip=ip, succeeded=False, attempted_at=now)
        )
        await store.append(
            session,
            type_=auth_events.LOGIN_FAILED.name,
            payload={"username": username, "ip": ip, "reason": reason},
            context=EventContext(Actor("user", actor_id), correlation_id=correlation_id),
            aggregate_type="user" if actor_id else None,
            aggregate_id=actor_id,
        )

    window_start = now - timedelta(minutes=settings.failure_window_minutes)
    if await _recent_failures(session, username, ip, window_start) >= settings.max_failures:
        await fail("throttled")
        raise TooManyAttempts("too many failed login attempts; try again later")

    user = await session.scalar(select(User).where(User.username == username))
    ok = await run_in_threadpool(verify_password, user.password_hash if user else None, password)
    if user is None or not ok:
        await fail("invalid_credentials", str(user.id) if user else None)
        raise NotAuthenticated("invalid username or password")
    if not user.is_active:
        await fail("inactive", str(user.id))
        raise NotAuthenticated("invalid username or password")

    if needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(hash_password, password)
        user.updated_at = now

    token = secrets.token_urlsafe(32)
    auth_session = AuthSession(
        id=uuid7(),
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    session.add(auth_session)
    session.add(
        LoginAttempt(id=uuid7(), username=username, ip=ip, succeeded=True, attempted_at=now)
    )
    user.last_login_at = now
    await session.flush()
    await store.append(
        session,
        type_=auth_events.LOGIN_SUCCEEDED.name,
        payload=auth_events.LoginSucceeded(
            username=username, session_id=str(auth_session.id), ip=ip
        ),
        context=EventContext(Actor("user", str(user.id)), correlation_id=ctx.correlation_id),
        aggregate_type="user",
        aggregate_id=str(user.id),
    )
    return LoginResult(user=user, session=auth_session, token=token)


async def authenticate(session: AsyncSession, token: str | None) -> Principal:
    """Resolve a bearer token (cookie value) to a principal or raise NotAuthenticated."""
    if not token:
        raise NotAuthenticated("authentication required")
    now = utcnow()
    row = (
        await session.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        raise NotAuthenticated("authentication required")
    auth_session, user = row
    if auth_session.revoked_at is not None or auth_session.expires_at <= now or not user.is_active:
        raise NotAuthenticated("session expired")
    if now - auth_session.last_seen_at >= _TOUCH_INTERVAL:
        await session.execute(
            update(AuthSession).where(AuthSession.id == auth_session.id).values(last_seen_at=now)
        )
    return Principal(user.id, user.username, Role(user.role), auth_session.id)


async def logout(
    session: AsyncSession, principal: Principal, *, correlation_id: str | None
) -> None:
    now = utcnow()
    await session.execute(
        update(AuthSession)
        .where(AuthSession.id == principal.session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await store.append(
        session,
        type_=auth_events.SESSION_REVOKED.name,
        payload=auth_events.SessionRevoked(session_id=str(principal.session_id), reason="logout"),
        context=EventContext(principal.actor, correlation_id=correlation_id),
        aggregate_type="user",
        aggregate_id=str(principal.user_id),
    )
