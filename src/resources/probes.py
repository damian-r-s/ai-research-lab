"""Resource probes: what hardware is actually present and how loaded it is.

Seed of the Resource Manager (spec §11). Values come from the OS (psutil) and NVIDIA's NVML.
A probe that cannot read a value reports ``status="unavailable"`` with a reason — it never
substitutes zeros or guesses.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import psutil

from src.core.clock import utcnow
from src.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CpuInfo:
    percent: float
    logical_cores: int
    physical_cores: int | None
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float
    swap_total_bytes: int
    swap_used_bytes: int


@dataclass(frozen=True, slots=True)
class DiskInfo:
    path: str
    mountpoint: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


@dataclass(frozen=True, slots=True)
class GpuDevice:
    index: int
    name: str
    memory_total_bytes: int
    memory_used_bytes: int
    utilization_percent: float | None
    temperature_c: float | None
    power_w: float | None


@dataclass(frozen=True, slots=True)
class GpuInfo:
    status: Literal["ok", "unavailable"]
    reason: str | None = None
    driver_version: str | None = None
    devices: tuple[GpuDevice, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    sampled_at: datetime
    hostname: str
    cpu: CpuInfo
    memory: MemoryInfo
    disks: tuple[DiskInfo, ...]
    gpu: GpuInfo
    warnings: tuple[str, ...] = field(default=())


GpuProbe = Callable[[], GpuInfo]


def probe_cpu(interval: float | None) -> CpuInfo:
    load1, load5, load15 = os.getloadavg()
    return CpuInfo(
        percent=psutil.cpu_percent(interval=interval),
        logical_cores=psutil.cpu_count(logical=True) or 0,
        physical_cores=psutil.cpu_count(logical=False),
        load_avg_1m=load1,
        load_avg_5m=load5,
        load_avg_15m=load15,
    )


def probe_memory() -> MemoryInfo:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return MemoryInfo(
        total_bytes=vm.total,
        used_bytes=vm.total - vm.available,
        available_bytes=vm.available,
        percent=vm.percent,
        swap_total_bytes=sw.total,
        swap_used_bytes=sw.used,
    )


def _mountpoint(path: str) -> str:
    p = os.path.realpath(path)
    while not os.path.ismount(p):
        p = os.path.dirname(p)
    return p


def probe_disks(paths: Sequence[str]) -> tuple[DiskInfo, ...]:
    seen: set[str] = set()
    out: list[DiskInfo] = []
    for path in paths:
        if not os.path.exists(path):
            continue
        mp = _mountpoint(path)
        if mp in seen:
            continue
        seen.add(mp)
        du = psutil.disk_usage(mp)
        out.append(DiskInfo(path, mp, du.total, du.used, du.free, du.percent))
    return tuple(out)


_nvml_lock = threading.Lock()
_nvml_initialised = False


def _ensure_nvml(pynvml: Any) -> None:
    """Initialise NVML once per process (first init can take over a second; reuse is cheap)."""
    global _nvml_initialised
    with _nvml_lock:
        if not _nvml_initialised:
            pynvml.nvmlInit()
            _nvml_initialised = True


def _reset_nvml(pynvml: Any) -> None:
    """After an NVML error, shut down so the next probe re-initialises from scratch."""
    global _nvml_initialised
    with _nvml_lock:
        if _nvml_initialised:
            with contextlib.suppress(pynvml.NVMLError):
                pynvml.nvmlShutdown()
            _nvml_initialised = False


def probe_nvidia() -> GpuInfo:
    """Query NVIDIA GPUs through NVML. Any failure → explicit 'unavailable' with the reason."""
    try:
        import pynvml
    except ImportError:
        return GpuInfo("unavailable", reason="nvidia-ml-py not installed")
    try:
        _ensure_nvml(pynvml)
    except pynvml.NVMLError as exc:
        return GpuInfo("unavailable", reason=f"NVML init failed: {exc}")
    try:
        driver = str(pynvml.nvmlSystemGetDriverVersion())
        devices = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = temp = power = None
            with contextlib.suppress(pynvml.NVMLError):
                util = float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
            with contextlib.suppress(pynvml.NVMLError):
                temp = float(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
            with contextlib.suppress(pynvml.NVMLError):
                power = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
            devices.append(
                GpuDevice(
                    index=i,
                    name=str(pynvml.nvmlDeviceGetName(h)),
                    memory_total_bytes=int(mem.total),
                    memory_used_bytes=int(mem.used),
                    utilization_percent=util,
                    temperature_c=temp,
                    power_w=power,
                )
            )
    except pynvml.NVMLError as exc:
        _reset_nvml(pynvml)
        return GpuInfo("unavailable", reason=f"NVML query failed: {exc}")
    if not devices:
        return GpuInfo("unavailable", reason="NVML reports no devices", driver_version=driver)
    return GpuInfo("ok", driver_version=driver, devices=tuple(devices))


@dataclass(frozen=True, slots=True)
class WarningThresholds:
    """When a reading becomes a warning. Hardware expectations are configuration, not code."""

    memory_percent: float = 90.0
    disk_percent: float = 90.0
    expect_gpu: bool = False  # a host without a GPU is normal unless configured otherwise


def _warnings(
    mem: MemoryInfo, disks: Sequence[DiskInfo], gpu: GpuInfo, limits: WarningThresholds
) -> tuple[str, ...]:
    out = []
    if mem.percent >= limits.memory_percent:
        out.append(f"memory usage high: {mem.percent:.0f}%")
    out.extend(
        f"disk {d.mountpoint} usage high: {d.percent:.0f}%"
        for d in disks
        if d.percent >= limits.disk_percent
    )
    if limits.expect_gpu and gpu.status != "ok":
        out.append(f"GPU expected but unavailable: {gpu.reason}")
    return tuple(out)


def take_snapshot(
    disk_paths: Sequence[str],
    *,
    gpu_probe: GpuProbe = probe_nvidia,
    cpu_interval: float | None,
    limits: WarningThresholds = WarningThresholds(),
) -> ResourceSnapshot:
    cpu = probe_cpu(cpu_interval)
    mem = probe_memory()
    disks = probe_disks(disk_paths)
    gpu = gpu_probe()
    return ResourceSnapshot(
        sampled_at=utcnow(),
        hostname=socket.gethostname(),
        cpu=cpu,
        memory=mem,
        disks=disks,
        gpu=gpu,
        warnings=_warnings(mem, disks, gpu, limits),
    )


class ResourceMonitor:
    """Samples resources periodically in a worker thread; readers get the latest snapshot.

    CPU percent is measured over the interval between samples, so it is meaningful from the
    first snapshot (taken with a short blocking interval at start-up).
    """

    def __init__(
        self,
        disk_paths: Sequence[str],
        interval_s: float,
        *,
        limits: WarningThresholds = WarningThresholds(),
        gpu_probe: GpuProbe = probe_nvidia,
    ) -> None:
        self._paths = list(disk_paths)
        self._interval = interval_s
        self._limits = limits
        self._gpu_probe = gpu_probe
        self._latest: ResourceSnapshot | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def latest(self) -> ResourceSnapshot | None:
        return self._latest

    async def start(self) -> None:
        self._latest = await asyncio.to_thread(
            take_snapshot,
            self._paths,
            gpu_probe=self._gpu_probe,
            cpu_interval=0.25,
            limits=self._limits,
        )
        self._task = asyncio.create_task(self._loop(), name="resource-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                self._latest = await asyncio.to_thread(
                    take_snapshot,
                    self._paths,
                    gpu_probe=self._gpu_probe,
                    cpu_interval=None,
                    limits=self._limits,
                )
            except Exception:
                log.exception("resource_monitor.sample_failed")
