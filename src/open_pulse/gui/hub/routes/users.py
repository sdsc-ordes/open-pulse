"""Admin "Users" — manage reader API tokens and view per-token activity.

All routes are admin-only (``require_admin``); mutations also require the hub
to be writable (``require_writable``). Reader tokens are the multi-tenant
successor to the single ``HUB_AUTH_READER`` password: each is revocable and its
calls are logged (see :mod:`..tokens`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import tokens
from ..auth import require_admin, require_auth, require_writable

router = APIRouter(prefix="/api/users", tags=["users"])

_READ = [Depends(require_auth), Depends(require_admin)]
_WRITE = [Depends(require_auth), Depends(require_admin), Depends(require_writable)]


@router.get("", dependencies=_READ)
def list_users() -> dict[str, Any]:
    """All reader tokens with call counts + last-seen (no secrets)."""
    return {"tokens": tokens.list_tokens()}


@router.post("", dependencies=_WRITE)
def create_user(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Mint a reader token. The plaintext is returned **once** — store it now.

    Optional ``graphs``: a named-graph allow-list. Non-empty → the token is
    scoped to those graphs; omitted/empty → a full reader."""
    label = (payload.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    graphs = payload.get("graphs")
    graphs = [g for g in graphs if isinstance(g, str)] if isinstance(graphs, list) else None
    return tokens.create_token(label, graphs=graphs)


@router.post("/{token_id}/scope", dependencies=_WRITE)
def set_scope(
    token_id: int, payload: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, Any]:
    """Set a token's named-graph ceiling. ``graphs=[]`` → full access."""
    graphs = payload.get("graphs")
    graphs = [g for g in graphs if isinstance(g, str)] if isinstance(graphs, list) else None
    if not tokens.set_scope(token_id, graphs):
        raise HTTPException(status_code=404, detail="token not found")
    return {"id": token_id, "graphs": graphs or []}


@router.post("/{token_id}/revoke", dependencies=_WRITE)
def revoke_user(token_id: int) -> dict[str, Any]:
    if not tokens.revoke_token(token_id):
        raise HTTPException(
            status_code=404, detail="token not found or already revoked"
        )
    return {"revoked": token_id}


@router.get("/{token_id}/activity", dependencies=_READ)
def user_activity(token_id: int) -> dict[str, Any]:
    """Recent calls + a breakdown by kind for one token (no IP)."""
    return tokens.token_activity(token_id)
