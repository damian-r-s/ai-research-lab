"""Liveness and readiness endpoints (used by k3s probes)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.state import AppState, get_app_state
from src.core.databases.health import check_database
from src.core.logging import get_logger

router = APIRouter(prefix="/api/health", tags=["health"])
log = get_logger(__name__)


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database_reachable: bool
    migrations_current: bool


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    """The process is running. Never touches dependencies."""
    return LiveResponse()


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
async def ready(state: Annotated[AppState, Depends(get_app_state)]) -> JSONResponse:
    """Ready to serve: database reachable and schema at the expected migration head.

    Failure details are logged, not returned, because this endpoint is unauthenticated.
    """
    db = await check_database(state.engine)
    if not db.ready:
        log.warning(
            "readiness.failed",
            database_reachable=db.reachable,
            current_revisions=db.current_revisions,
            expected_revisions=db.expected_revisions,
            error=db.error,
        )
    body = ReadyResponse(
        status="ready" if db.ready else "not_ready",
        database_reachable=db.reachable,
        migrations_current=db.migrations_current,
    )
    return JSONResponse(body.model_dump(), status_code=200 if db.ready else 503)
