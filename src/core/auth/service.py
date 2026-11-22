"""Authentication use cases. Callers own the transaction; every state change emits an event."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.core.auth import events as auth_events
from src.core.clock import utcnow
from src.core.config import AuthSettings
from src.core.databases.models.users import AuthSession, User
from src.core.databases.repositories import users_repo
from src.core.errors import ConflictError, NotAuthenticated, TooManyAttempts
from src.core.events import store
from src.core.events.store import Actor, EventContext
from src.core.ids import uuid7
from src.core.security import (
    Role,
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    verify_password,
)

# Sessions are touched at most this often, to avoid a write on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)
# One message for every credential failure, so responses do not reveal which usernames exist.
_INVALID = "invalid username or password"


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
    """Create an account. Raises WeakPassword or ConflictError."""
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
        raise ConflictError(f"user {username!r} already exists or has an invalid name") from exc
    await store.append(
        session,
        type_=auth_events.USER_CREATED.name,
        payload=auth_events.UserCreated(username=username, role=role.value),
        context=context,
        aggregate_type="user",
        aggregate_id=str(user.id),
    )
    return user


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

    On failure, the attempt and an ``auth.login.failed`` event are flushed and an exception is
    raised. The caller must still COMMIT, so the failure is durably recorded: that is what makes
    throttling survive restarts.
    """
    username = normalise_username(username)
    now = utcnow()

    async def record_failure(reason: str, user: User | None) -> None:
        users_repo.add_login_attempt(session, username=username, ip=ip, succeeded=False, at=now)
        actor_id = str(user.id) if user else None
        await store.append(
            session,
            type_=auth_events.LOGIN_FAILED.name,
            payload=auth_events.LoginFailed(username=username, ip=ip, reason=reason),
            context=EventContext(Actor("user", actor_id), correlation_id=correlation_id),
            aggregate_type="user" if actor_id else None,
            aggregate_id=actor_id,
        )

    since = now - timedelta(minutes=settings.failure_window_minutes)
    failures = await users_repo.count_recent_failures(
        session, username=username, ip=ip, since=since
    )
    if failures >= settings.max_failures:
        await record_failure("throttled", None)
        raise TooManyAttempts("too many failed login attempts; try again later")

    user = await users_repo.get_by_username(session, username)
    ok = await run_in_threadpool(verify_password, user.password_hash if user else None, password)
    if user is None or not ok:
        await record_failure("invalid_credentials", user)
        raise NotAuthenticated(_INVALID)
    if not user.is_active:
        await record_failure("inactive", user)
        raise NotAuthenticated(_INVALID)

    if needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(hash_password, password)
        user.updated_at = now

    token = new_session_token()
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
    users_repo.add_login_attempt(session, username=username, ip=ip, succeeded=True, at=now)
    user.last_login_at = now
    await session.flush()
    await store.append(
        session,
        type_=auth_events.LOGIN_SUCCEEDED.name,
        payload=auth_events.LoginSucceeded(
            username=username, session_id=str(auth_session.id), ip=ip
        ),
        context=EventContext(Actor("user", str(user.id)), correlation_id=correlation_id),
        aggregate_type="user",
        aggregate_id=str(user.id),
    )
    return LoginResult(user=user, session=auth_session, token=token)


async def authenticate(session: AsyncSession, token: str | None) -> Principal:
    """Resolve a session token (cookie value) to a principal, or raise NotAuthenticated."""
    if not token:
        raise NotAuthenticated("authentication required")
    found = await users_repo.get_session_with_user(session, hash_token(token))
    if found is None:
        raise NotAuthenticated("authentication required")
    auth_session, user = found
    now = utcnow()
    if auth_session.revoked_at is not None or auth_session.expires_at <= now:
        raise NotAuthenticated("session expired; please log in again")
    if not user.is_active:
        raise NotAuthenticated("account is disabled")
    if now - auth_session.last_seen_at >= _TOUCH_INTERVAL:
        await users_repo.touch_session(session, auth_session.id, now)
    return Principal(user.id, user.username, Role(user.role), auth_session.id)


async def logout(
    session: AsyncSession, principal: Principal, *, correlation_id: str | None
) -> None:
    if not await users_repo.revoke_session(session, principal.session_id, utcnow()):
        return  # already revoked: idempotent, nothing to record
    await store.append(
        session,
        type_=auth_events.SESSION_REVOKED.name,
        payload=auth_events.SessionRevoked(session_id=str(principal.session_id), reason="logout"),
        context=EventContext(principal.actor, correlation_id=correlation_id),
        aggregate_type="user",
        aggregate_id=str(principal.user_id),
    )
