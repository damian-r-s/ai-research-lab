"""System status for the dashboard: real resource readings, database and audit-log state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src import VERSION
from src.api.deps import DbSession, State, require_role
from src.core.auth.service import Principal
from src.core.clock import utcnow
from src.core.databases.health import check_database
from src.core.events import store
from src.core.security import Role
from src.resources.probes import CpuInfo, DiskInfo, GpuInfo, MemoryInfo

router = APIRouter(prefix="/api/system", tags=["system"])
Viewer = Annotated[Principal, Depends(require_role(Role.viewer))]


class ResourcesOut(BaseModel):
    sampled_at: datetime
    hostname: str
    cpu: CpuInfo
    memory: MemoryInfo
    disks: list[DiskInfo]
    gpu: GpuInfo


class DatabaseOut(BaseModel):
    reachable: bool
    migrations_current: bool
    current_revisions: list[str]


class AuditOut(BaseModel):
    total_events: int
    last_seq: int | None
    live_updates: Literal["connected", "reconnecting"]


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    started_at: datetime
    uptime_s: float
    resources: ResourcesOut | None  # None only before the first sample completes
    database: DatabaseOut
    audit: AuditOut
    warnings: list[str]


@router.get("/status", response_model=SystemStatus)
async def status(_: Viewer, state: State, session: DbSession) -> SystemStatus:
    db = await check_database(state.engine)
    total, last_seq = await store.stats(session)
    snapshot = state.monitor.latest
    warnings = list(snapshot.warnings) if snapshot else ["resource monitor has no sample yet"]
    if not db.migrations_current:
        warnings.append("database schema is not at the expected migration")
    live = "connected" if state.notifier.connected.is_set() else "reconnecting"
    if live != "connected":
        warnings.append("live event updates are reconnecting; the dashboard may lag")
    now = utcnow()
    return SystemStatus(
        status="ok" if not warnings else "degraded",
        version=VERSION,
        env=state.settings.env,
        started_at=state.started_at,
        uptime_s=round((now - state.started_at).total_seconds(), 1),
        resources=(
            ResourcesOut(
                sampled_at=snapshot.sampled_at,
                hostname=snapshot.hostname,
                cpu=snapshot.cpu,
                memory=snapshot.memory,
                disks=list(snapshot.disks),
                gpu=snapshot.gpu,
            )
            if snapshot
            else None
        ),
        database=DatabaseOut(
            reachable=db.reachable,
            migrations_current=db.migrations_current,
            current_revisions=list(db.current_revisions),
        ),
        audit=AuditOut(total_events=total, last_seq=last_seq, live_updates=live),
        warnings=warnings,
    )
