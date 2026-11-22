"""Event types emitted by the auth module."""

from __future__ import annotations

from typing import Literal

from src.core.events.registry import EventPayload, register


class UserCreated(EventPayload):
    username: str
    role: str


class LoginSucceeded(EventPayload):
    username: str
    session_id: str
    ip: str | None


class LoginFailed(EventPayload):
    username: str
    ip: str | None
    reason: Literal["invalid_credentials", "inactive", "throttled"]


class SessionRevoked(EventPayload):
    session_id: str
    reason: Literal["logout", "admin"]


USER_CREATED = register("auth.user.created", 1, UserCreated, "A user account was created")
LOGIN_SUCCEEDED = register("auth.login.succeeded", 1, LoginSucceeded, "Successful login")
LOGIN_FAILED = register("auth.login.failed", 1, LoginFailed, "Rejected login attempt")
SESSION_REVOKED = register("auth.session.revoked", 1, SessionRevoked, "Session ended")
