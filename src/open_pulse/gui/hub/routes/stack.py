"""Stack control: bring profiles up / down by exec'ing the deploy CLI."""

from __future__ import annotations

from typing import Any

import docker
from docker.errors import NotFound
from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import require_auth, require_writable

router = APIRouter(prefix="/api/stack", tags=["stack"])

_CLI_CONTAINER = "open-pulse-cli"

# Mirrors src/open_pulse/commands/deploy.py:_PROFILES — kept in sync manually
# rather than importing from open_pulse so the hub image doesn't need the
# package on its python path. If the list drifts, the worst case is the UI
# misses a new profile until the next hub release.
PROFILES: list[dict[str, str]] = [
    {"name": "default", "label": "Core (Neo4j only)"},
    {"name": "crawler", "label": "Open Pulse Crawler API"},
    {"name": "extractor", "label": "GME extractor + Selenium"},
    {"name": "sparql", "label": "Oxigraph + sparql-proxy"},
    {"name": "hub", "label": "This dashboard"},
    {"name": "grimoirelab", "label": "GrimoireLab DB & worker (main compose)"},
    {"name": "orchestration", "label": "Portainer"},
]


def _client() -> docker.DockerClient:
    return docker.from_env()


def _cli_container() -> Any:
    try:
        return _client().containers.get(_CLI_CONTAINER)
    except NotFound:
        raise HTTPException(
            status_code=503,
            detail=(
                "open-pulse-cli container is not running. "
                "Bring it up with `--profile hub` (or `--with-cli`) first."
            ),
        )


@router.get("/profiles", dependencies=[Depends(require_auth)])
def list_profiles() -> dict[str, Any]:
    return {"profiles": PROFILES}


@router.post("/up",
             dependencies=[Depends(require_auth), Depends(require_writable)])
def stack_up(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    profiles = list(payload.get("profiles") or [])
    with_grimoire = bool(payload.get("with_grimoire", False))
    if not profiles and not with_grimoire:
        raise HTTPException(
            status_code=400,
            detail="At least one profile (or with_grimoire) is required.",
        )

    cmd = ["open-pulse", "deploy", "up"]
    for p in profiles:
        cmd.extend(["--profile", p])
    if with_grimoire:
        cmd.append("--with-grimoire")

    cli = _cli_container()
    rc, out = cli.exec_run(cmd=cmd, stdout=True, stderr=True)
    return {
        "exit_code": rc,
        "command": cmd,
        "output": (out or b"").decode("utf-8", "replace"),
    }


@router.post("/down",
             dependencies=[Depends(require_auth), Depends(require_writable)])
def stack_down(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    with_grimoire = bool(payload.get("with_grimoire", False))
    remove_volumes = bool(payload.get("remove_volumes", False))

    cmd = ["open-pulse", "deploy", "down"]
    if with_grimoire:
        cmd.append("--with-grimoire")
    if remove_volumes:
        cmd.append("--volumes")

    cli = _cli_container()
    rc, out = cli.exec_run(cmd=cmd, stdout=True, stderr=True)
    return {
        "exit_code": rc,
        "command": cmd,
        "output": (out or b"").decode("utf-8", "replace"),
    }


@router.get("/ps", dependencies=[Depends(require_auth)])
def stack_ps(with_grimoire: bool = False) -> dict[str, Any]:
    cmd = ["open-pulse", "deploy", "ps"]
    if with_grimoire:
        cmd.append("--with-grimoire")
    cli = _cli_container()
    rc, out = cli.exec_run(cmd=cmd, stdout=True, stderr=True)
    return {
        "exit_code": rc,
        "command": cmd,
        "output": (out or b"").decode("utf-8", "replace"),
    }
