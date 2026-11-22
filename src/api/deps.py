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


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The real client address, or None if it is not an IP (Unix socket, test client name).

    X-Forwarded-For is honoured only when the direct peer is a configured trusted proxy. The
    chain is read from the right, skipping trusted proxies; the first other address is the
    client. Values a client prepends to the header itself are therefore never used.
    """
    state: AppState = request.app.state.lab
    trusted = [ipaddress.ip_network(c, strict=False) for c in state.settings.api.trusted_proxies]
    peer = _parse_ip(request.client.host) if request.client else None
    if peer is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not any(peer in net for net in trusted):
        return str(peer)
    client = peer
    for hop in reversed(forwarded.split(",")):
        address = _parse_ip(hop)
        if address is None:
            break  # malformed hop: stop at the last address we could trust
        client = address
        if not any(address in net for net in trusted):
            break
    return str(client)
