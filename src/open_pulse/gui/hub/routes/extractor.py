"""Hub-gated proxy for the git-metadata-extractor (GME) Swagger UI.

GME ships its OpenAPI spec at ``/openapi.json`` and Swagger UI at
``/docs``. The upstream API endpoints are bearer-token protected, but
the docs themselves are public on the published port. Proxying through
the hub means hub-authenticated users can discover the API surface
without us also having to expose the upstream port to the world.

Only the read-only docs surface is proxied here — Swagger UI's
"Try it out" is wired to hit the upstream public URL directly (with the
user's GME bearer pasted into Authorize), so we don't need to forward
every API path through the hub.
"""

from __future__ import annotations

import json
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from ..auth import require_auth

router = APIRouter(prefix="/api/extractor", tags=["extractor"])


def _extractor_base() -> str:
    """In-network URL of the GME container."""
    return os.environ.get(
        "HUB_EXTRACTOR_URL", "http://git-metadata-extractor:1234"
    ).rstrip("/")


def _extractor_public_url() -> str:
    """External base URL Swagger UI's "Try it out" hits."""
    explicit = os.environ.get("HUB_EXTRACTOR_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("HUB_PUBLIC_HOST", "localhost").strip() or "localhost"
    port = os.environ.get("EXTRACTOR_PORT", "1234").strip() or "1234"
    return f"http://{host}:{port}"


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
    try:
        upstream = httpx.get(f"{_extractor_base()}/openapi.json", timeout=5.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    spec = upstream.json()
    spec["servers"] = [{"url": _extractor_public_url()}]
    return Response(content=json.dumps(spec), media_type="application/json")
