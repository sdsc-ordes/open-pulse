"""Service tiles: list, status, start / stop / restart, tail logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth, require_writable
from ..docker_client import container_action, list_services, tail_logs

router = APIRouter(prefix="/api/services", tags=["services"])


class ActionRequest(BaseModel):
    action: str  # start | stop | restart


@router.get("/", dependencies=[Depends(require_auth)])
def list_all() -> dict[str, object]:
    return {"services": list_services()}


@router.post(
    "/{name}/action", dependencies=[Depends(require_auth), Depends(require_writable)]
)
def perform_action(name: str, body: ActionRequest) -> dict[str, object]:
    if body.action not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="invalid action")
    return container_action(name, body.action)


@router.get("/{name}/logs", dependencies=[Depends(require_auth)])
def logs(name: str, tail: int = 200) -> dict[str, str]:
    if tail <= 0 or tail > 5000:
        raise HTTPException(status_code=400, detail="tail must be in 1..5000")
    return {"name": name, "logs": tail_logs(name, tail=tail)}
