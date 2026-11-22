"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import VERSION
from src.api import auth, errors, events, health, system, users
from src.api.middleware import CsrfMiddleware, RequestContextMiddleware
from src.api.state import AppState
from src.core.clock import utcnow
from src.core.config import Settings, get_settings
from src.core.databases.database import create_engine, create_session_factory, libpq_url
from src.core.events.notifier import EventNotifier
from src.core.events.system import PROCESS_STARTED, PROCESS_STOPPED, record_lifecycle
from src.core.logging import configure_logging, get_logger
from src.resources.probes import GpuProbe, ResourceMonitor, WarningThresholds, probe_nvidia

log = get_logger(__name__)


COMPONENT = "api"


def create_app(settings: Settings | None = None, *, gpu_probe: GpuProbe = probe_nvidia) -> FastAPI:
    """Build the API. `gpu_probe` is injectable so hardware-specific paths can be tested."""
    settings = settings or get_settings()
    configure_logging(settings.logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database)
        factory = create_session_factory(engine)
        notifier = EventNotifier(libpq_url(settings.database.url.get_secret_value()))
        res = settings.resources
        monitor = ResourceMonitor(
            [*res.disk_paths, str(settings.data_dir)],
            res.sample_interval_s,
            limits=WarningThresholds(
                res.memory_warn_percent, res.disk_warn_percent, res.expect_gpu
            ),
            gpu_probe=gpu_probe,
        )
        await notifier.start()  # connects in the background and reconnects on failure
        await monitor.start()
        app.state.lab = AppState(settings, engine, factory, notifier, monitor, utcnow())
        await record_lifecycle(factory, PROCESS_STARTED.name, COMPONENT)
        log.info("api.started", env=settings.env, version=VERSION)
        try:
            yield
        finally:
            await record_lifecycle(factory, PROCESS_STOPPED.name, COMPONENT)
            await monitor.stop()
            await notifier.stop()
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
    errors.install(app)
    # Outermost first: request context wraps everything, so CSRF rejections are logged too.
    app.add_middleware(CsrfMiddleware, header_name=settings.api.csrf_header)
    app.add_middleware(RequestContextMiddleware)
    for router in (health.router, auth.router, users.router, events.router, system.router):
        app.include_router(router)
    return app
