"""Single-password gate for the hub.

The hub is single-tenant — anyone who knows the shared HUB_AUTH password
can read / control / launch / stop services. We use HTTP Basic with a fixed
username (``admin``) so any browser can prompt natively, plus a tiny cookie
session so the password isn't in every request after first login.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, load_settings

_SETTINGS = load_settings()
_basic = HTTPBasic(auto_error=False)

# In-memory session store: cookie value -> any (we only need existence).
# The hub is single-process and short-lived; no need for Redis here.
_SESSIONS: set[str] = set()
_COOKIE_NAME = "op_hub_session"


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def get_settings() -> Settings:
    return _SETTINGS


def issue_session(response: Response) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS.add(token)
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,  # 12h
    )
    return token


def clear_session(response: Response, token: str | None) -> None:
    if token:
        _SESSIONS.discard(token)
    response.delete_cookie(_COOKIE_NAME)


def require_auth(
    request: Request,
    response: Response,
    creds: Annotated[HTTPBasicCredentials | None, Depends(_basic)] = None,
    op_hub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Accept either a valid session cookie or correct Basic credentials.

    On a successful Basic auth, we also issue a session cookie so subsequent
    requests skip the credential check.
    """
    if op_hub_session and op_hub_session in _SESSIONS:
        return

    if creds is not None and _const_eq(creds.password, _SETTINGS.auth_token):
        issue_session(response)
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Basic realm="open-pulse-hub"'},
    )


def maybe_require_auth(
    request: Request,
    response: Response,
    creds: Annotated[HTTPBasicCredentials | None, Depends(_basic)] = None,
    op_hub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Auth bypass for /hub/** when HUB_PUBLIC_KNOWLEDGE=true.

    Same accept-rules as :func:`require_auth`, but a flip of the env flag
    turns the knowledge surface into a fully public catalog. Mounted on
    the hub routes only; the rest of the dashboard stays gated.
    """
    if _SETTINGS.public_knowledge:
        return
    require_auth(request, response, creds, op_hub_session)


def require_writable() -> None:
    """Block every mutating endpoint when HUB_READONLY=true.

    The dependency goes alongside ``require_auth`` on routes that change
    server-side state — stack up/down, container start/stop/restart,
    projects apply, pipeline run/stop, crawler job control. Read-only
    queries (databases/duckdb/query, ai/chat, dashboards) skip this
    dependency so the hub stays usable in observer mode.

    Raises HTTP 403 with a stable JSON shape so a UI button click and a
    direct curl both get the same readable error.
    """
    if _SETTINGS.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Hub is running in read-only mode (HUB_READONLY=true). "
                "Mutating endpoints are disabled — run the change from "
                "the operator CLI instead."
            ),
        )
