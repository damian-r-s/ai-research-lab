"""Request-scoped dependencies: database session, authenticated principal, role checks."""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.state import AppState, get_app_state
from src.core.auth.service import Principal, authenticate
from src.core.errors import PermissionDenied
from src.core.security import Role

State = Annotated[AppState, Depends(get_app_state)]


async def get_session(state: State) -> AsyncIterator[AsyncSession]:
    """One session per request. Routes commit explicitly; anything uncommitted is rolled back."""
    async with state.session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def session_token(request: Request, state: AppState) -> str | None:
    return request.cookies.get(state.settings.auth.cookie_name)


async def current_principal(request: Request, state: State) -> Principal:
    """Authenticate from the session cookie, in its own short transaction (session touch)."""
    async with state.session_factory() as session:
        principal = await authenticate(session, session_token(request, state))
        await session.commit()
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_role(role: Role) -> Callable[[Principal], Awaitable[Principal]]:
    async def check(principal: CurrentPrincipal) -> Principal:
        if not principal.role.satisfies(role):
            raise PermissionDenied(f"this action requires the {role.value} role")
        return principal

    return check


def request_id(request: Request) -> str | None:
    rid: str | None = request.scope.get("state", {}).get("request_id")
    return rid


def client_ip(request: Request) -> str | None:
    """The peer address if it is a real IP (not e.g. a Unix socket or test client name).

    Behind a reverse proxy this is the proxy's address; trusting X-Forwarded-For is a deliberate
    deployment decision (only from the known ingress), made when the ingress is set up.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None
