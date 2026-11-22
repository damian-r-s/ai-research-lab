"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import VERSION
from src.api import health
from src.api.state import AppState
from src.core.config import Settings, get_settings
from src.core.databases.database import create_engine, create_session_factory
from src.core.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database)
        app.state.lab = AppState(settings, engine, create_session_factory(engine))
        log.info("api.started", env=settings.env, version=VERSION)
        try:
            yield
        finally:
            await engine.dispose()
            log.info("api.stopped")

    app = FastAPI(
        title="AI Research & Innovation Lab",
        version=VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app
