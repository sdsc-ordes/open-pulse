"""Pass-through proxies for the open-pulse-crawler API.

The hub doesn't own any crawler state — these routes just forward to the
crawler service so the browser can pause/resume/cancel/delete jobs from
the same auth-gated origin (and so we don't have to teach the UI about
the crawler's bearer token, which lives in HUB env / Settings).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth

router = APIRouter(prefix="/api/crawler", tags=["crawler"])


def _crawler_base() -> str:
    # In-network URL — the crawler container is published as `crawler` on
    # the compose network. Override with HUB_CRAWLER_URL if needed.
    return os.environ.get("HUB_CRAWLER_URL", "http://crawler:8000").rstrip("/")


def _crawler_token() -> str:
    """The bearer the crawler API requires.

    The crawler reads ``CRAWLER_API_TOKEN`` from its own env at start; the
    hub container loads the same value from the project ``.env`` (we pass
    ``CRAWLER_API_TOKEN`` straight through in the compose env_file).
    """
    return os.environ.get("CRAWLER_API_TOKEN", "")


def _client() -> httpx.Client:
    token = _crawler_token()
    if not token:
        raise HTTPException(
            status_code=500,
            detail="CRAWLER_API_TOKEN not set in the hub container's env. "
            "Add it to your .env (the project's CRAWLER_API_TOKEN) and "
            "restart the hub.",
        )
    return httpx.Client(
        base_url=_crawler_base(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )


def _passthrough(method: str, path: str, **kw: Any) -> dict[str, Any]:
    with _client() as c:
        try:
            resp = c.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if resp.status_code >= 400:
        # Try to surface the upstream's `detail`, fall back to the body.
        try:
            j = resp.json()
            detail = j.get("detail") or j
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json() if resp.content else {}


@router.get("/jobs", dependencies=[Depends(require_auth)])
def list_jobs() -> dict[str, Any]:
    return _passthrough("GET", "/api/v1/jobs")


@router.get("/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job(job_id: str) -> dict[str, Any]:
    return _passthrough("GET", f"/api/v1/crawl/{job_id}")


@router.post("/jobs/{job_id}/pause", dependencies=[Depends(require_auth)])
def pause_job(job_id: str) -> dict[str, Any]:
    return _passthrough("POST", f"/api/v1/crawl/{job_id}/pause")


@router.post("/jobs/{job_id}/resume", dependencies=[Depends(require_auth)])
def resume_job(job_id: str) -> dict[str, Any]:
    return _passthrough("POST", f"/api/v1/crawl/{job_id}/resume")


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
def cancel_job(job_id: str) -> dict[str, Any]:
    return _passthrough("POST", f"/api/v1/crawl/{job_id}/cancel")


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_auth)])
def delete_job(job_id: str) -> dict[str, Any]:
    return _passthrough("DELETE", f"/api/v1/crawl/{job_id}")
