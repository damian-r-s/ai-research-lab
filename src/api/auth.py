"""Login, logout and the current user."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from src.api.deps import CurrentPrincipal, DbSession, State, client_ip, request_id
from src.api.schemas import LoginRequest, MeResponse
from src.core.auth import service
from src.core.errors import NotAuthenticated, TooManyAttempts
from src.core.security import Role

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=MeResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: DbSession, state: State
) -> MeResponse:
    settings = state.settings.auth
    try:
        result = await service.login(
            session,
            username=body.username,
            password=body.password,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            settings=settings,
            correlation_id=request_id(request),
        )
    except (NotAuthenticated, TooManyAttempts):
        await session.commit()  # persist the failed attempt (throttling) and its audit event
        raise
    await session.commit()
    response.set_cookie(
        settings.cookie_name,
        result.token,
        max_age=int(settings.session_ttl_hours * 3600),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return MeResponse(username=result.user.username, role=Role(result.user.role))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    session: DbSession,
    state: State,
) -> Response:
    await service.logout(session, principal, correlation_id=request_id(request))
    await session.commit()
    response.status_code = 204
    response.delete_cookie(
        state.settings.auth.cookie_name,
        path="/",
        httponly=True,
        secure=state.settings.auth.cookie_secure,
        samesite="strict",
    )
    return response


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal) -> MeResponse:
    return MeResponse(username=principal.username, role=principal.role)
