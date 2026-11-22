"""Criteria 1.11, 1.12 and 1.14: health probes, real system status, survival across restarts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import psutil
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.core.config import AuthSettings, DatabaseSettings, ResourceSettings, Settings
from src.core.databases.database import create_engine, create_session_factory
from src.core.events.store import verify_chain
from src.resources.probes import GpuInfo, probe_nvidia
from test.conftest import PASSWORD, ScratchDatabase, make_user, sql

pytestmark = pytest.mark.integration

CSRF = {"X-Lab-CSRF": "1"}
UNREACHABLE = "postgresql+psycopg://lab_app@127.0.0.1:1/lab"  # nothing listens on port 1


def _settings(db_url: str, owner_url: str | None = None, **resources: Any) -> Settings:
    return Settings(
        env="test",
        database=DatabaseSettings(url=db_url, migration_url=owner_url),
        auth=AuthSettings(cookie_secure=False),
        resources=ResourceSettings(**resources),
    )


def _no_gpu() -> GpuInfo:
    return GpuInfo("unavailable", reason="no NVIDIA GPU detected (test probe)")


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}, headers=CSRF
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def status_client(fresh_db: ScratchDatabase) -> Iterator[TestClient]:
    make_user(fresh_db, "watcher", "viewer")
    with TestClient(create_app(_settings(fresh_db.app_url, fresh_db.owner_url))) as c:
        _login(c, "watcher")
        yield c


# --- 1.11 health ----------------------------------------------------------------------------


def test_app_starts_and_reports_not_ready_when_the_database_is_unreachable() -> None:
    with TestClient(create_app(_settings(UNREACHABLE), gpu_probe=_no_gpu)) as client:
        assert client.get("/api/health/live").status_code == 200  # process is alive
        ready = client.get("/api/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "database_reachable": False,
        "migrations_current": False,
    }
    assert "127.0.0.1" not in ready.text and "refused" not in ready.text.lower()  # no internals


def test_ready_flips_back_when_migrations_are_applied(fresh_db: ScratchDatabase) -> None:
    from src.core.databases.migrate import downgrade, upgrade

    with TestClient(create_app(_settings(fresh_db.app_url, fresh_db.owner_url))) as client:
        assert client.get("/api/health/ready").status_code == 200
        downgrade("base", url=fresh_db.owner_url, app_role="lab_app")
        assert client.get("/api/health/ready").status_code == 503
        upgrade("head", url=fresh_db.owner_url, app_role="lab_app")
        assert client.get("/api/health/ready").status_code == 200


# --- 1.12 status reflects reality -----------------------------------------------------------


def test_status_requires_login(fresh_db: ScratchDatabase) -> None:
    with TestClient(create_app(_settings(fresh_db.app_url))) as client:
        assert client.get("/api/system/status").status_code == 401


def test_status_values_match_an_independent_reading(status_client: TestClient) -> None:
    body = status_client.get("/api/system/status").json()
    res = body["resources"]
    vm = psutil.virtual_memory()
    assert res["memory"]["total_bytes"] == vm.total
    assert abs(res["memory"]["available_bytes"] - vm.available) < 0.05 * vm.total
    assert res["cpu"]["logical_cores"] == psutil.cpu_count(logical=True)
    assert 0.0 <= res["cpu"]["percent"] <= 100.0
    root = next(d for d in res["disks"] if d["mountpoint"] == "/")
    assert root["total_bytes"] == psutil.disk_usage("/").total
    assert abs(root["used_bytes"] - psutil.disk_usage("/").used) < 1024**3  # within 1 GiB


def test_gpu_reading_matches_nvml_or_says_why_not(status_client: TestClient) -> None:
    """On a GPU host the devices match NVML; on a host without one the status says why."""
    gpu = status_client.get("/api/system/status").json()["resources"]["gpu"]
    independent = probe_nvidia()
    assert gpu["status"] == independent.status
    if independent.status == "ok":
        assert [d["name"] for d in gpu["devices"]] == [d.name for d in independent.devices]
        assert [d["memory_total_bytes"] for d in gpu["devices"]] == [
            d.memory_total_bytes for d in independent.devices
        ]
    else:
        assert gpu["devices"] == [] and gpu["reason"]


def test_missing_gpu_is_explicit_never_zeros(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "watcher", "viewer")
    app = create_app(_settings(fresh_db.app_url, fresh_db.owner_url), gpu_probe=_no_gpu)
    with TestClient(app) as client:
        _login(client, "watcher")
        body = client.get("/api/system/status").json()
    gpu = body["resources"]["gpu"]
    assert gpu == {
        "status": "unavailable",
        "reason": "no NVIDIA GPU detected (test probe)",
        "driver_version": None,
        "devices": [],
    }
    # No GPU is normal unless this host is configured to expect one.
    assert not any("GPU" in w for w in body["warnings"])


def test_expected_but_missing_gpu_degrades_status(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "watcher", "viewer")
    settings = _settings(fresh_db.app_url, fresh_db.owner_url, expect_gpu=True)
    with TestClient(create_app(settings, gpu_probe=_no_gpu)) as client:
        _login(client, "watcher")
        body = client.get("/api/system/status").json()
    assert body["status"] == "degraded"
    assert "GPU expected but unavailable: no NVIDIA GPU detected (test probe)" in body["warnings"]


def test_warning_thresholds_come_from_configuration(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "watcher", "viewer")
    settings = _settings(fresh_db.app_url, fresh_db.owner_url, memory_warn_percent=0.1)
    with TestClient(create_app(settings, gpu_probe=_no_gpu)) as client:
        _login(client, "watcher")
        body = client.get("/api/system/status").json()
    assert any(w.startswith("memory usage high") for w in body["warnings"])
    assert body["status"] == "degraded"


def test_status_reports_database_and_audit_state(
    status_client: TestClient, fresh_db: ScratchDatabase
) -> None:
    body = status_client.get("/api/system/status").json()
    ((count, last),) = sql(fresh_db.owner_url, "SELECT count(*), max(seq) FROM lab.events")
    assert body["audit"]["total_events"] == count
    assert body["audit"]["last_seq"] == last
    assert body["database"]["reachable"] is True
    assert body["database"]["migrations_current"] is True
    assert body["version"] and body["env"] == "test"
    assert body["uptime_s"] >= 0


# --- 1.14 restart ---------------------------------------------------------------------------


def test_state_survives_a_restart_and_restarts_are_audited(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "boss", "admin")
    settings = _settings(fresh_db.app_url, fresh_db.owner_url)

    with TestClient(create_app(settings, gpu_probe=_no_gpu)) as first:
        _login(first, "boss")
        created = first.post(
            "/api/users",
            json={"username": "survivor", "password": PASSWORD, "role": "viewer"},
            headers=CSRF,
        )
        assert created.status_code == 201
    # first instance fully shut down here (engine disposed, lifecycle "stopped" recorded)

    with TestClient(create_app(settings, gpu_probe=_no_gpu)) as second:
        _login(second, "survivor")  # account created by the previous instance still works
        assert second.get("/api/auth/me").json()["username"] == "survivor"

    lifecycle = [
        r[0]
        for r in sql(
            fresh_db.owner_url,
            "SELECT type FROM lab.events WHERE type LIKE %s ORDER BY seq",
            ("system.process.%",),
        )
    ]
    assert lifecycle == [
        "system.process.started",
        "system.process.stopped",
        "system.process.started",
        "system.process.stopped",
    ]

    async def verify() -> bool:
        engine = create_engine(settings.database)
        try:
            async with create_session_factory(engine)() as session:
                return (await verify_chain(session)).ok
        finally:
            await engine.dispose()

    assert asyncio.run(verify())  # the audit chain is intact across both process lifetimes
