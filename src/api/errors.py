"""One error format for every API failure: {"error": {"code": ..., "message": ...}}."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.errors import (
    ConflictError,
    LabError,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    TooManyAttempts,
)
from src.core.security import WeakPassword

_STATUS: dict[type[LabError], int] = {
    NotAuthenticated: 401,
    PermissionDenied: 403,
    NotFound: 404,
    ConflictError: 409,
    TooManyAttempts: 429,
}


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


async def _lab_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LabError)
    status = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 400)
    headers = {"WWW-Authenticate": "Cookie"} if status == 401 else None
    return JSONResponse(error_body(exc.code, exc.message), status_code=status, headers=headers)


async def _weak_password(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(error_body("weak_password", str(exc)), status_code=422)


def install(app: FastAPI) -> None:
    app.add_exception_handler(LabError, _lab_error)
    app.add_exception_handler(WeakPassword, _weak_password)
