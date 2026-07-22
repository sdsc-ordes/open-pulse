"""Projects.json applier — tiny FastAPI sidecar.

Endpoints:

- ``GET  /healthz``   — unauthenticated liveness; for the compose healthcheck.
- ``GET  /current``   — return the projects.json currently on the Mordred volume.
- ``POST /apply``     — write a new projects.json atomically and restart Mordred.

Auth: bearer token from the ``APPLIER_AUTH`` env var. ``GET /current`` and
``POST /apply`` both require it; ``/healthz`` is open.

Env knobs:
- ``APPLIER_AUTH``                 — required bearer token.
- ``CONF_DIR``                     — defaults to ``/conf``. The directory
                                     mordred reads ``projects.json`` from.
- ``MORDRED_CONTAINER_NAME``       — defaults to ``open-pulse-mordred``.
                                     Sent to ``docker restart``.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("applier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="Open Pulse projects.json applier",
    version="0.1.0",
)

# CORS: the dashboard UI is served cross-origin (different port than the
# applier), so the browser needs an explicit allow. Bearer auth still
# protects the endpoints — CORS just removes the preflight rejection.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_bearer = HTTPBearer(auto_error=False)


def _conf_dir() -> Path:
    return Path(os.environ.get("CONF_DIR", "/conf"))


def _projects_path() -> Path:
    return _conf_dir() / "projects.json"


# GitHub-API backends Mordred needs to collect issues + PRs + repo metadata
# (as opposed to the ``git`` backend, which only clones commits).
_GITHUB_BACKENDS = ("github:issue", "github:pull", "github:repo")


def _derive_github_backends(payload: dict[str, Any]) -> None:
    """Ensure every group that has a ``git`` list also collects GitHub issues,
    PRs, and repo metadata — so the issue/PR/CR CHAOSS metrics have data, not
    just commit metrics.

    Every projects.json write goes through this endpoint, so deriving here makes
    issue/PR collection correct-by-construction for *any* group source (quests,
    owner-grouped build, hub UI, CLI) — no per-writer drift.

    The GitHub API needs ``owner/repo`` without the ``.git`` suffix that cloning
    uses, so we strip it. Idempotent and non-destructive: only fills a backend a
    group hasn't already declared, so a group can opt out or customise by
    setting e.g. ``"github:issue": []`` explicitly.
    """
    for group in payload.values():
        if not isinstance(group, dict):
            continue
        git = group.get("git")
        if not isinstance(git, list) or not git:
            continue
        clean = sorted(
            {
                u[:-4] if u.endswith(".git") else u
                for u in git
                if isinstance(u, str)
            }
        )
        for backend in _GITHUB_BACKENDS:
            if backend not in group:
                group[backend] = clean


def _expected_token() -> str:
    token = os.environ.get("APPLIER_AUTH", "")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APPLIER_AUTH is not configured on the server",
        )
    return token


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    expected = _expected_token()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
    return credentials.credentials


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/current", tags=["projects"])
def current(_token: str = Depends(verify_token)) -> dict[str, Any]:
    path = _projects_path()
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No projects.json yet at {path}",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"projects.json on disk is not valid JSON: {exc}",
        ) from exc


@app.post("/apply", tags=["projects"])
def apply(
    payload: dict[str, Any],
    _token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Write the supplied projects.json atomically and restart Mordred."""
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be a non-empty JSON object",
        )

    # Correct-by-construction: every group that clones a repo also collects its
    # issues/PRs/repo metadata, regardless of which writer built the payload.
    _derive_github_backends(payload)

    conf_dir = _conf_dir()
    conf_dir.mkdir(parents=True, exist_ok=True)
    target = conf_dir / "projects.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    logger.info("wrote %s (%d top-level groups)", target, len(payload))

    mordred = os.environ.get("MORDRED_CONTAINER_NAME", "open-pulse-mordred")
    try:
        result = subprocess.run(
            ["docker", "restart", mordred],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="docker CLI not available in the applier container",
        ) from exc

    if result.returncode != 0:
        logger.warning(
            "docker restart %s failed: %s", mordred, result.stderr.strip()[:200]
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"projects.json written but `docker restart {mordred}` failed: "
                f"{result.stderr.strip()[:200]}"
            ),
        )

    logger.info("restarted %s", mordred)
    return {
        "status": "applied",
        "detail": f"projects.json written, restarted {mordred}",
        "groups": list(payload.keys()),
    }
