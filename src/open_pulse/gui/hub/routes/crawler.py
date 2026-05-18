"""Pass-through proxies for the open-pulse-crawler API.

The hub doesn't own any crawler state — these routes just forward to the
crawler service so the browser can pause/resume/cancel/delete jobs from
the same auth-gated origin (and so we don't have to teach the UI about
the crawler's bearer token, which lives in HUB env / Settings).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

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


def _crawler_public_url() -> str:
    """External base URL Swagger UI's "Try it out" hits.

    Defaults to ``http://${HUB_PUBLIC_HOST}:${CRAWLER_PORT}`` so a single
    knob keeps everything pointing at the right host. Override with
    ``HUB_CRAWLER_PUBLIC_URL`` if the crawler sits behind a different
    proxy (e.g. https + path-prefix).
    """
    explicit = os.environ.get("HUB_CRAWLER_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("HUB_PUBLIC_HOST", "localhost").strip() or "localhost"
    port = os.environ.get("CRAWLER_PORT", "8000").strip() or "8000"
    return f"http://{host}:{port}"


@router.get("/docs", include_in_schema=False, dependencies=[Depends(require_auth)])
def crawler_docs() -> HTMLResponse:
    """Hub-gated Swagger UI for the crawler API. Reads the spec from
    ``/api/crawler/openapi.json`` (also hub-gated). Authentication is the
    user's hub session — no upstream token needed to *view* the surface.
    """
    return get_swagger_ui_html(
        openapi_url="/api/crawler/openapi.json",
        title="Crawler API — via Open Pulse Hub",
    )


@router.get(
    "/openapi.json", include_in_schema=False, dependencies=[Depends(require_auth)]
)
def crawler_openapi() -> Response:
    """Proxy the upstream spec and rewrite ``servers`` to the crawler's
    public URL so Swagger UI's "Try it out" hits the upstream directly
    (the user pastes ``CRAWLER_API_TOKEN`` into the Authorize dialog).
    """
    try:
        upstream = httpx.get(f"{_crawler_base()}/api/v1/openapi.json", timeout=5.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    spec = upstream.json()
    spec["servers"] = [{"url": _crawler_public_url()}]
    return Response(content=json.dumps(spec), media_type="application/json")


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
