"""Thin wrapper around the docker SDK for the hub.

We talk to the host's docker daemon via the bind-mounted /var/run/docker.sock.
Only read + simple lifecycle operations are exposed — no exec, no build,
no volume management. The hub is a control panel, not a substitute for
a shell.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Any

import docker
from docker.errors import NotFound


def _uptime_seconds(started_at_iso: str | None) -> int | None:
    if not started_at_iso:
        return None
    try:
        s = started_at_iso.replace("Z", "+00:00")
        if "." in s:
            head, _, tail = s.partition(".")
            if "+" in tail or "-" in tail[1:]:
                idx = max(tail.find("+"), tail.find("-", 1))
                tail = tail[:idx][:6] + tail[idx:]
            else:
                tail = tail[:6]
            s = f"{head}.{tail}"
        dt = datetime.fromisoformat(s)
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    except (ValueError, TypeError):
        return None


@functools.lru_cache(maxsize=1)
def _client() -> docker.DockerClient:
    return docker.from_env()


# Containers that the hub considers "part of the open-pulse stack" — used to
# filter out unrelated containers running on the host. The match is by name
# prefix or substring; everything else is hidden by default.
_NAME_HINTS = (
    "open-pulse",
    "neo4j-open-pulse",
    "oxigraph-open-pulse",
    "sparql-proxy",
    "applier-live",
    "ui-live",
    # GrimoireLab service names
    "mariadb",
    "valkey",
    "opensearch-node",
    "opensearch-dashboards",
    "mordred",
    "sortinghat",
    "nginx",
    "projects-applier",
)


def _is_open_pulse(name: str) -> bool:
    lname = name.lstrip("/")
    return any(hint in lname for hint in _NAME_HINTS)


def list_services() -> list[dict[str, Any]]:
    """Return one row per open-pulse-related container."""
    out: list[dict[str, Any]] = []
    for c in _client().containers.list(all=True):
        name = c.name
        if not _is_open_pulse(name):
            continue
        attrs = c.attrs
        state = attrs.get("State", {})
        health = (state.get("Health") or {}).get("Status")
        labels = attrs.get("Config", {}).get("Labels") or {}
        started_at = state.get("StartedAt")
        out.append(
            {
                "name": name,
                "image": attrs.get("Config", {}).get("Image"),
                "status": c.status,           # running / exited / created / ...
                "health": health,             # healthy / unhealthy / starting / None
                "started_at": started_at,
                "uptime_seconds": _uptime_seconds(started_at),
                "ports": _format_ports(attrs.get("NetworkSettings", {}).get("Ports")),
                "compose_service": labels.get("com.docker.compose.service"),
                "compose_project": labels.get("com.docker.compose.project"),
            }
        )
    out.sort(key=lambda r: r["name"])
    return out


def _format_ports(ports: dict[str, Any] | None) -> list[str]:
    if not ports:
        return []
    formatted: list[str] = []
    for container_port, bindings in ports.items():
        if not bindings:
            continue
        for b in bindings:
            host = b.get("HostIp") or "0.0.0.0"
            host_port = b.get("HostPort")
            if host_port:
                formatted.append(f"{host}:{host_port}->{container_port}")
    return formatted


def container_action(name: str, action: str) -> dict[str, Any]:
    """start / stop / restart a container by name. Returns post-action state."""
    if action not in {"start", "stop", "restart"}:
        raise ValueError(f"unsupported action: {action!r}")
    try:
        c = _client().containers.get(name)
    except NotFound:
        return {"ok": False, "error": f"container {name!r} not found"}
    getattr(c, action)()
    c.reload()
    return {
        "ok": True,
        "name": c.name,
        "status": c.status,
    }


def tail_logs(name: str, tail: int = 200) -> str:
    try:
        c = _client().containers.get(name)
    except NotFound:
        return f"<container {name!r} not found>"
    raw = c.logs(tail=tail, timestamps=False)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)
