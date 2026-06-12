"""Custom login surface for the Open Pulse hub.

Replaces the bare browser-native Basic Auth dialog with a styled HTML
form (``templates/login.html``) that explains the project, then issues
the same session cookie the rest of the hub already trusts. The
existing ``auth.require_auth`` dependency is unchanged — it still
accepts the session cookie or a Basic credential, so curl / Postman /
the test suite all keep working untouched.

Routes:
    GET  /login          → render the form (optionally pre-fills ``next``
                           from the query string so a redirect from
                           require_auth-via-exception-handler bounces back
                           to the original URL after sign-in).
    POST /login          → validate the password against ``HUB_AUTH``
                           and, on success, issue the session cookie
                           and redirect to ``next`` (or ``/hub/``).
    POST /logout         → drop the session cookie. Redirects to /login
                           so the user lands somewhere meaningful.

A global 401-to-redirect exception handler lives in ``main.py`` —
that's the piece that turns the existing ``raise HTTPException(401)``
calls into a redirect to ``/login`` *only when* the incoming request
looks like a browser (HTML accept) rather than an API client.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import _match_role, clear_session, issue_session

# Templates live next to the routes package; same directory as base.html.
_TEMPLATES = Jinja2Templates(
    directory=os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "templates",
    ),
)

router = APIRouter(tags=["auth"])


def _safe_next(raw: str | None) -> str:
    """Sanitize the ``next`` redirect target.

    Only allow same-origin relative paths starting with ``/`` and
    explicitly forbid protocol-relative URLs (``//evil.example.com``)
    so an attacker can't craft a phishing redirect via the login flow.
    Falls back to ``/hub/`` on anything fishy.
    """
    if not raw:
        return "/hub/"
    # Reject anything that looks like an off-site URL.
    if raw.startswith("//") or "://" in raw:
        return "/hub/"
    if not raw.startswith("/"):
        return "/hub/"
    # Sanity check: make sure urlsplit agrees it's a path-only URL.
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return "/hub/"
    return raw


@router.get("/login", include_in_schema=False)
def login_form(
    request: Request,
    next: str | None = None,
    error: int | None = None,
) -> HTMLResponse:
    # Starlette 1.x signature: request is the first positional argument
    # and the context dict no longer needs to embed `request` itself.
    return _TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {
            "next": _safe_next(next),
            "error": bool(error),
        },
    )


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/hub/"),
) -> RedirectResponse:
    target = _safe_next(next)
    role = _match_role(password)
    if role is None:
        # Bounce back to the form with ?error=1 and the original ?next=
        # preserved so a second attempt still lands where the user wanted.
        return RedirectResponse(
            f"/login?error=1&next={target}",
            status_code=303,
        )
    # Issue the cookie + stamp the role. 303 converts the POST into a GET
    # so the browser re-renders the destination page cleanly. Readers who
    # try to land on an expert-only page (e.g. /pipeline) will hit the
    # template's role check, not a server-side redirect — keeps the URL
    # they typed intact for a possible later upgrade.
    resp = RedirectResponse(target, status_code=303)
    issue_session(resp, role)
    return resp


@router.post("/logout", include_in_schema=False)
def logout(
    op_hub_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    clear_session(resp, op_hub_session)
    return resp
