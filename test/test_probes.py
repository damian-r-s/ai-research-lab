"""Resource probes: failure paths are explicit and recover; warnings follow configuration."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from src.resources import probes
from src.resources.probes import (
    DiskInfo,
    GpuInfo,
    MemoryInfo,
    WarningThresholds,
    _warnings,
    probe_nvidia,
)


class FakeNVMLError(Exception):
    pass


def fake_nvml(*, fail_init: bool = False, fail_query: bool = False, devices: int = 1) -> Any:
    calls = {"init": 0, "shutdown": 0}
    m: Any = types.ModuleType("pynvml")
    m.NVMLError = FakeNVMLError
    m.NVML_TEMPERATURE_GPU = 0

    def init() -> None:
        calls["init"] += 1
        if fail_init:
            raise FakeNVMLError("driver not loaded")

    def driver() -> str:
        if fail_query:
            raise FakeNVMLError("GPU is lost")
        return "999.1"

    m.nvmlInit = init
    m.nvmlShutdown = lambda: calls.__setitem__("shutdown", calls["shutdown"] + 1)
    m.nvmlSystemGetDriverVersion = driver
    m.nvmlDeviceGetCount = lambda: devices
    m.nvmlDeviceGetHandleByIndex = lambda i: i
    m.nvmlDeviceGetMemoryInfo = lambda h: types.SimpleNamespace(total=96 * 1024**3, used=1024**3)
    m.nvmlDeviceGetUtilizationRates = lambda h: types.SimpleNamespace(gpu=37)
    m.nvmlDeviceGetTemperature = lambda h, kind: 55
    m.nvmlDeviceGetPowerUsage = lambda h: 300_000
    m.nvmlDeviceGetName = lambda h: f"Fake GPU {h}"
    m.calls = calls
    return m


@pytest.fixture(autouse=True)
def reset_nvml_state() -> Iterator[None]:
    probes._nvml_initialised = False
    yield
    probes._nvml_initialised = False


def use(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setitem(sys.modules, "pynvml", module)


def test_healthy_gpu_is_reported_with_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, fake_nvml(devices=2))
    info = probe_nvidia()
    assert info.status == "ok" and info.driver_version == "999.1"
    assert [d.name for d in info.devices] == ["Fake GPU 0", "Fake GPU 1"]
    d = info.devices[0]
    assert (d.memory_total_bytes, d.utilization_percent, d.temperature_c, d.power_w) == (
        96 * 1024**3,
        37.0,
        55.0,
        300.0,
    )


def test_nvml_is_initialised_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_nvml()
    use(monkeypatch, fake)
    for _ in range(5):
        probe_nvidia()
    assert fake.calls == {"init": 1, "shutdown": 0}


def test_init_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, fake_nvml(fail_init=True))
    assert probe_nvidia() == GpuInfo("unavailable", reason="NVML init failed: driver not loaded")


def test_query_failure_is_explicit_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = fake_nvml(fail_query=True)
    use(monkeypatch, broken)
    assert probe_nvidia() == GpuInfo("unavailable", reason="NVML query failed: GPU is lost")
    assert broken.calls["shutdown"] == 1  # state reset after the error
    healthy = fake_nvml()
    use(monkeypatch, healthy)
    assert probe_nvidia().status == "ok"
    assert healthy.calls["init"] == 1  # re-initialised from scratch


def test_no_devices_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, fake_nvml(devices=0))
    info = probe_nvidia()
    assert info.status == "unavailable" and info.reason == "NVML reports no devices"


def test_missing_library_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pynvml", None)  # makes `import pynvml` raise ImportError
    assert probe_nvidia() == GpuInfo("unavailable", reason="nvidia-ml-py not installed")


def _mem(percent: float) -> MemoryInfo:
    return MemoryInfo(100, int(percent), 100 - int(percent), percent, 0, 0)


def _disk(percent: float) -> DiskInfo:
    return DiskInfo("/", "/", 100, int(percent), 100 - int(percent), percent)


def test_warnings_follow_thresholds() -> None:
    gpu = GpuInfo("unavailable", reason="none")
    limits = WarningThresholds(memory_percent=80, disk_percent=85, expect_gpu=False)
    assert _warnings(_mem(79), [_disk(84)], gpu, limits) == ()
    assert _warnings(_mem(80), [_disk(85)], gpu, limits) == (
        "memory usage high: 80%",
        "disk / usage high: 85%",
    )
    expecting = WarningThresholds(expect_gpu=True)
    assert _warnings(_mem(1), [], gpu, expecting) == ("GPU expected but unavailable: none",)
