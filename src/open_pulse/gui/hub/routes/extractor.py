"""Hub-gated proxy for the git-metadata-extractor (GME) — Swagger UI
*and* the v2/* API surface.

Why this exists
---------------
The GME container publishes its API on a host port (default 1234), but
the EPFL firewall in front of the openpulse.epfl.ch VM only opens the
75xx range — the hub itself runs on 7507, and 1234 is unreachable from
outside the host. Two consequences:

1. Browser users could load Swagger UI at the hub origin
   (``/api/extractor/docs``) but the previous code wired Swagger's
   "Try it out" to hit the upstream public URL ``http://<host>:1234``
   directly, which fails from outside.
2. Programmatic clients on operator laptops can't reach the upstream
   either.

This module solves both by proxying the upstream's API endpoints under
``/api/extractor/v2/…`` on the hub. Auth is the hub's session auth (the
same gate that already covers ``/docs`` and ``/openapi.json``); the
hub server-side injects the GME bearer (``EXTRACTOR_API_TOKEN``) so the
user never has to paste it. Same shape as ``routes/crawler.py``.

What changes for the user
-------------------------
- ``http://openpulse.epfl.ch:7507/api/extractor/docs`` — Swagger UI
  (loads ``/api/extractor/openapi.json`` which now lists
  ``servers: [{"url": "/api/extractor"}]`` so "Try it out" hits the
  hub proxy).
- ``POST /api/extractor/v2/extract`` — submit an extract job; body
  identical to the upstream's contract.
- ``GET  /api/extractor/v2/jobs/{job_id}`` — poll for status / result.
- Any other ``/api/extractor/v2/…`` path is forwarded verbatim (path,
  query string, body, content-type), so future upstream endpoints work
  without a hub change.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from ..auth import require_auth

router = APIRouter(prefix="/api/extractor", tags=["extractor"])


def _extractor_base() -> str:
    """In-network URL of the GME container."""
    return os.environ.get(
        "HUB_EXTRACTOR_URL", "http://git-metadata-extractor:1234"
    ).rstrip("/")


def _extractor_token() -> str:
    """Bearer the GME API requires.

    The hub container loads ``EXTRACTOR_API_TOKEN`` via env_file at start;
    the GME reads the same value as ``API_TOKEN`` server-side. Missing
    here means the hub can't proxy — surface a clear 500 so the operator
    sets it rather than getting a silent 401 from upstream.
    """
    return os.environ.get("EXTRACTOR_API_TOKEN", "")


# Methods the GME's FastAPI app implements. Keep this in sync with the
# OpenAPI spec if a new verb appears. Catching unknown methods early
# saves an upstream round-trip and a misleading 405.
_PROXIED_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


@router.get("/docs", include_in_schema=False, dependencies=[Depends(require_auth)])
def extractor_docs() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/api/extractor/openapi.json",
        title="Metadata extractor API — via Open Pulse Hub",
    )


@router.get(
    "/openapi.json", include_in_schema=False, dependencies=[Depends(require_auth)]
)
def extractor_openapi() -> Response:
    """Pass through the upstream OpenAPI spec, rewriting ``servers:`` so
    Swagger UI's "Try it out" hits the hub proxy (same origin as the
    docs page) instead of the unreachable upstream port.

    We also strip the ``security`` requirements: the hub auto-injects
    the GME bearer server-side, so asking the user to paste it into
    Swagger's "Authorize" dialog would be confusing and could leak the
    token into browser memory unnecessarily.
    """
    try:
        upstream = httpx.get(f"{_extractor_base()}/openapi.json", timeout=5.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    spec: dict[str, Any] = upstream.json()
    # Point Swagger UI back at the hub proxy. Relative path — works
    # whether the hub is accessed via openpulse.epfl.ch:7507, an SSH
    # forward, or any internal alias, without needing HUB_PUBLIC_HOST.
    spec["servers"] = [{"url": "/api/extractor"}]
    # Hub does the auth; clear per-operation security so Swagger UI
    # doesn't gate "Try it out" behind the now-unnecessary HTTPBearer
    # dialog. Keep the components.securitySchemes intact for reference.
    if isinstance(spec.get("paths"), dict):
        for ops in spec["paths"].values():
            if not isinstance(ops, dict):
                continue
            for op in ops.values():
                if isinstance(op, dict) and "security" in op:
                    op["security"] = []
    spec.setdefault("security", [])
    return Response(content=json.dumps(spec), media_type="application/json")


@router.api_route(
    "/v2/{path:path}",
    methods=list(_PROXIED_METHODS),
    include_in_schema=False,
    dependencies=[Depends(require_auth)],
)
async def extractor_v2_proxy(path: str, request: Request) -> Response:
    """Forward ``/api/extractor/v2/{path}`` to the GME container.

    Hub session auth gates the route; the GME bearer is injected
    server-side. The body, query string, content-type, and method are
    preserved end-to-end. A long client-side timeout (15 min) matches
    the longest extract a single repo can take in hybrid mode.
    """
    token = _extractor_token()
    if not token:
        raise HTTPException(
            status_code=500,
            detail=(
                "EXTRACTOR_API_TOKEN not set in the hub container's env. "
                "Add it to infra/.env and restart the hub."
            ),
        )

    body = await request.body()
    forward_headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
    content_type = request.headers.get("content-type")
    if content_type:
        forward_headers["Content-Type"] = content_type

    upstream_url = f"{_extractor_base()}/v2/{path}"
    try:
        async with httpx.AsyncClient(timeout=900.0) as client:
            upstream = await client.request(
                method=request.method,
                url=upstream_url,
                content=body if body else None,
                params=dict(request.query_params),
                headers=forward_headers,
            )
    except httpx.HTTPError as exc:
        # 502 = bad gateway — we couldn't talk to the upstream.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Pass through the upstream's status + body. Filter hop-by-hop /
    # connection-specific headers — uvicorn handles those itself and
    # passing them through can confuse downstream proxies.
    excluded = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "upgrade",
    }
    out_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in excluded
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
