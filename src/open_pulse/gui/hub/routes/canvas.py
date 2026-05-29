"""Knowledge-graph canvas — full-bleed Excalidraw embed with a Cmd+K
entity-search modal.

Users hit ⌘/Ctrl+K, type to search any hub collection (autocompletion
flows through the existing ``/api/hub/autocomplete`` endpoint), and
click a suggestion to drop the entity onto an Excalidraw canvas as a
node. The rest of the page is vanilla Excalidraw: native multi-select,
freedraw, arrows, shapes, sticky notes — everything bound to the
Minecraft-style number-key shortcuts Excalidraw ships with.

This is a deliberate spike — no per-user persistence, no server-side
canvas store. Excalidraw mounts via ESM imports from a CDN; the rest of
the hub stays on Alpine + Jinja so the slot is cheap to rip out if the
approach doesn't scale.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..auth import maybe_require_auth

router = APIRouter(tags=["canvas"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get(
    "/canvas",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def canvas_home(request: Request) -> HTMLResponse:
    """Render the canvas page. All canvas state lives client-side."""
    return templates.TemplateResponse(
        request,
        "canvas.html",
        {"page": "canvas"},
    )
