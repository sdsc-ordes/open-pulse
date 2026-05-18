"""System-resource stats: disk, RAM, CPU, Docker.

Lightweight admin endpoint the hub UI polls (15-min cadence by default)
to surface "are we close to the wall" — the kind of question that's
better answered before the disk-full event, not after.

All numbers come from stdlib + the Docker SDK; no psutil dependency
because the hub image doesn't ship it.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

import docker
from docker.errors import DockerException
from fastapi import APIRouter, Depends

from ..auth import require_auth

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger(__name__)

# Mounts we care about inside the hub container. `/data` is the host's
# OPEN_PULSE_DATA_DIR (the disk that just filled up); `/` is the hub's
# own writable layer — small, but worth a sanity gauge.
_DISK_PATHS = [
    ("/data", "data"),
    ("/", "container_root"),
]

_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_TTL = 30.0  # seconds


def _disk_usage(path: str) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "pct_used": round(100 * usage.used / usage.total, 1) if usage.total else 0.0,
    }


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of {key: kilobytes}.

    /proc is not namespaced for these fields under default Docker so the
    values reflect the host kernel, which is what we want.
    """
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                rest = rest.strip()
                if rest.endswith(" kB"):
                    rest = rest[:-3]
                try:
                    out[key] = int(rest.strip())
                except ValueError:
                    continue
    except OSError:
        return {}
    return out


def _mem_stats() -> dict[str, Any] | None:
    meminfo = _read_meminfo()
    if not meminfo:
        return None
    # Modern kernels expose MemAvailable directly — much more accurate
    # than MemFree (which excludes reclaimable cache).
    total_kb = meminfo.get("MemTotal", 0)
    avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    used_kb = max(0, total_kb - avail_kb)
    return {
        "total_bytes": total_kb * 1024,
        "available_bytes": avail_kb * 1024,
        "used_bytes": used_kb * 1024,
        "pct_used": round(100 * used_kb / total_kb, 1) if total_kb else 0.0,
        "swap_total_bytes": swap_total * 1024,
        "swap_used_bytes": max(0, (swap_total - swap_free)) * 1024,
    }


def _cpu_stats() -> dict[str, Any] | None:
    """Load average from /proc/loadavg + core count from os.cpu_count.

    Load average is a host-wide metric — Docker doesn't virtualise
    /proc/loadavg either, so the values are global.
    """
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            parts = f.read().split()
    except OSError:
        return None
    if len(parts) < 3:
        return None
    cores = os.cpu_count() or 1
    try:
        l1, l5, l15 = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None
    return {
        "load_1m": l1,
        "load_5m": l5,
        "load_15m": l15,
        "cores": cores,
        # Per-core load is the most useful "is it saturated?" gauge.
        "load_1m_per_core": round(l1 / cores, 2),
    }


def _docker_stats() -> dict[str, Any]:
    """Image / volume / container space via the Docker SDK.

    Mirrors ``docker system df`` numbers. Doesn't poll per-container
    runtime stats (those are expensive — one call per container).
    """
    try:
        client = docker.from_env()
        # The Docker SDK exposes ``df()`` on the underlying API (returns
        # the same JSON the CLI shows). Wrap in best-effort: not every
        # daemon version honours it identically.
        api_df = client.api.df()
    except DockerException as exc:
        log.debug("docker df failed: %s", exc)
        return {}

    images = api_df.get("Images") or []
    containers = api_df.get("Containers") or []
    volumes = api_df.get("Volumes") or []
    return {
        "images": {
            "count": len(images),
            "size_bytes": sum(int(i.get("Size") or 0) for i in images),
            "reclaimable_bytes": sum(
                int(i.get("Size") or 0)
                for i in images
                if (i.get("Containers") or 0) == 0
            ),
        },
        "containers": {
            "count": len(containers),
            "running": sum(
                1 for c in containers if (c.get("State") or "").lower() == "running"
            ),
            "rw_bytes": sum(int(c.get("SizeRw") or 0) for c in containers),
        },
        "volumes": {
            "count": len(volumes),
            "size_bytes": sum(
                int((v.get("UsageData") or {}).get("Size") or 0) for v in volumes
            ),
            # Volumes with no linked containers are reclaimable via
            # ``docker volume prune``.
            "dangling_bytes": sum(
                int((v.get("UsageData") or {}).get("Size") or 0)
                for v in volumes
                if ((v.get("UsageData") or {}).get("RefCount") or 0) == 0
            ),
        },
    }


def _gather() -> dict[str, Any]:
    disks: dict[str, Any] = {}
    for path, key in _DISK_PATHS:
        info = _disk_usage(path)
        if info:
            info["mount"] = path
            disks[key] = info
    return {
        "disks": disks,
        "memory": _mem_stats(),
        "cpu": _cpu_stats(),
        "docker": _docker_stats(),
        "thresholds": {
            # The UI flips a row to "warning" / "critical" using these.
            # 75 % / 90 % is a tight-enough early-warning band — we
            # crossed 95 % yesterday before noticing.
            "disk_warn_pct": 75.0,
            "disk_crit_pct": 90.0,
            "mem_warn_pct": 80.0,
            "mem_crit_pct": 92.0,
            "load_warn_per_core": 1.0,
            "load_crit_per_core": 2.0,
        },
        "generated_at": int(time.time()),
    }


@router.get("/resources", dependencies=[Depends(require_auth)])
def resources() -> dict[str, Any]:
    """Return the latest cached resources snapshot.

    Cached for ``_TTL`` seconds so a curious user clicking refresh
    doesn't hammer Docker's API. The polling UI requests this every
    15 min by default — the cache is a courtesy, not a load shield.
    """
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]
    data = _gather()
    _CACHE["data"] = data
    _CACHE["at"] = now
    return data
