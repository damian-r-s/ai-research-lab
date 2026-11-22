"""Typed access to process-wide resources created in the app lifespan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.core.events.notifier import EventNotifier
from src.resources.probes import ResourceMonitor


@dataclass(frozen=True, slots=True)
class AppState:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    notifier: EventNotifier
    monitor: ResourceMonitor
    started_at: datetime


def get_app_state(request: Request) -> AppState:
    state: AppState = request.app.state.lab
    return state
