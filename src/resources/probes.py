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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

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


def probe_nvidia() -> GpuInfo:
    """Query NVIDIA GPUs through NVML. Any failure → explicit 'unavailable'."""
    try:
        import pynvml
    except ImportError:
        return GpuInfo("unavailable", reason="nvidia-ml-py not installed")
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError as exc:
        return GpuInfo("unavailable", reason=f"NVML init failed: {exc}")
    try:
        driver = pynvml.nvmlSystemGetDriverVersion()
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
        if not devices:
            return GpuInfo("unavailable", reason="NVML reports no devices", driver_version=driver)
        return GpuInfo("ok", driver_version=str(driver), devices=tuple(devices))
    except pynvml.NVMLError as exc:
        return GpuInfo("unavailable", reason=f"NVML query failed: {exc}")
    finally:
        with contextlib.suppress(pynvml.NVMLError):
            pynvml.nvmlShutdown()


def _warnings(mem: MemoryInfo, disks: Sequence[DiskInfo], gpu: GpuInfo) -> tuple[str, ...]:
    out = []
    if mem.percent >= 90:
        out.append(f"memory usage high: {mem.percent:.0f}%")
    out.extend(
        f"disk {d.mountpoint} usage high: {d.percent:.0f}%" for d in disks if d.percent >= 90
    )
    if gpu.status != "ok":
        out.append(f"GPU unavailable: {gpu.reason}")
    return tuple(out)


def take_snapshot(
    disk_paths: Sequence[str], *, gpu_probe: GpuProbe = probe_nvidia, cpu_interval: float | None
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
        warnings=_warnings(mem, disks, gpu),
    )


class ResourceMonitor:
    """Samples resources periodically in a worker thread; readers get the latest snapshot.

    CPU percent is measured over the interval between samples, so it is meaningful from the
    first snapshot (taken with a short blocking interval at start-up).
    """

    def __init__(
        self, disk_paths: Sequence[str], interval_s: float, gpu_probe: GpuProbe = probe_nvidia
    ) -> None:
        self._paths = list(disk_paths)
        self._interval = interval_s
        self._gpu_probe = gpu_probe
        self._latest: ResourceSnapshot | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def latest(self) -> ResourceSnapshot | None:
        return self._latest

    async def start(self) -> None:
        self._latest = await asyncio.to_thread(
            take_snapshot, self._paths, gpu_probe=self._gpu_probe, cpu_interval=0.25
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
                    take_snapshot, self._paths, gpu_probe=self._gpu_probe, cpu_interval=None
                )
            except Exception:
                log.exception("resource_monitor.sample_failed")
