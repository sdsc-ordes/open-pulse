"""Role-aware password gate for the hub.

Two passwords, two roles:

  * ``HUB_AUTH``         — admin. Full UI, every mutating endpoint
                           allowed (subject to HUB_READONLY).
  * ``HUB_AUTH_READER``  — reader. Same read endpoints as admin, but
                           ``require_writable`` rejects with 403 and
                           the sidebar drops the operator-only tabs
                           (Stack, Settings, Quests, GrimoireLab
                           Projects). Optional — leave empty to keep
                           admin-only behaviour.

A successful login (form or Basic) issues a session cookie whose
server-side entry stamps the role. ``require_writable`` checks the
role on every protected route; the template context exposes it as
``request.state.user_role`` so the sidebar / per-page UI can hide
controls a reader can't use.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated, Literal

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import tokens
from .config import Settings, load_settings

_SETTINGS = load_settings()
_basic = HTTPBasic(auto_error=False)

Role = Literal["admin", "reader"]

# In-memory session store: cookie value -> (role, reader_token_id).
# The hub is single-process and short-lived; no need for Redis here. token_id
# is set only for sessions authenticated by a DB-managed reader token, so a
# cookie-based follow-up request still logs against the right token.
_SESSIONS: dict[str, tuple[Role, int | None]] = {}
_COOKIE_NAME = "op_hub_session"


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _match_role(password: str) -> Role | None:
    """Return the role the password authenticates as, or None on mismatch.

    Admin wins ties — if somebody set HUB_AUTH and HUB_AUTH_READER to the
    same value (don't), we honour the higher-privilege match. The reader
    arm is skipped when ``auth_token_reader`` is empty so the legacy
    single-password deploy stays untouched.
    """
    if _const_eq(password, _SETTINGS.auth_token):
        return "admin"
    if _SETTINGS.auth_token_reader and _const_eq(
        password, _SETTINGS.auth_token_reader
    ):
        return "reader"
    return None


def _match_credential(password: str) -> tuple[Role, int | None] | None:
    """Resolve a presented secret to ``(role, reader_token_id)``.

    Admin password → ``("admin", None)``; the env ``HUB_AUTH_READER`` →
    ``("reader", None)``; otherwise a DB-managed reader token →
    ``("reader", <id>)``. ``None`` on mismatch. The token id lets us attribute
    per-token activity for the Users panel."""
    role = _match_role(password)
    if role is not None:
        return (role, None)
    tid = tokens.match_token(password)
    if tid is not None:
        return ("reader", tid)
    return None


def get_settings() -> Settings:
    return _SETTINGS


def session_info(token: str | None) -> tuple[Role | None, int | None]:
    """(role, reader_token_id) for a session cookie value, or (None, None)."""
    if not token:
        return (None, None)
    return _SESSIONS.get(token, (None, None))


def session_role(token: str | None) -> Role | None:
    """Look up the role attached to a session cookie value, or None if
    the cookie is missing / expired / forged."""
    return session_info(token)[0]


def issue_session(
    response: Response, role: Role = "admin", token_id: int | None = None
) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = (role, token_id)
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
        _SESSIONS.pop(token, None)
    response.delete_cookie(_COOKIE_NAME)


def require_auth(
    request: Request,
    response: Response,
    creds: Annotated[HTTPBasicCredentials | None, Depends(_basic)] = None,
    op_hub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Accept either a valid session cookie or correct Basic credentials.

    Sets ``request.state.user_role`` (``"admin"`` or ``"reader"``) so
    downstream routes + templates can branch on it. On a successful
    Basic auth, we also issue a session cookie so subsequent requests
    skip the credential check.
    """
    role, token_id = session_info(op_hub_session)
    if role is not None:
        request.state.user_role = role
        request.state.token_id = token_id
        if token_id is not None:
            tokens.log_access(
                token_id,
                request.method,
                request.url.path
                + (f"?{request.url.query}" if request.url.query else ""),
            )
        return

    if creds is not None:
        matched = _match_credential(creds.password)
        if matched is not None:
            role, token_id = matched
            issue_session(response, role, token_id)
            request.state.user_role = role
            request.state.token_id = token_id
            if token_id is not None:
                tokens.log_access(
                    token_id,
                    request.method,
                    request.url.path
                    + (f"?{request.url.query}" if request.url.query else ""),
                )
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


def require_writable(
    request: Request,
    op_hub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Block every mutating endpoint when either:

      - ``HUB_READONLY=true`` is set on the hub (global kill-switch),
      - the calling session is a reader (logged in with HUB_AUTH_READER).

    Composed with ``require_auth`` on routes that change server-side
    state — stack up/down, container start/stop/restart, projects
    apply, pipeline run/stop, crawler job control. Read-only queries
    (databases/duckdb/query, ai/chat, dashboards) skip this dependency
    so the hub stays usable in observer mode.

    Returns 403 with a stable JSON shape so a UI button click and a
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
    # `request.state.user_role` is populated by require_auth running
    # earlier in the dependency chain on every protected route. Fall
    # back to a cookie lookup so this dep also works when wired alone.
    role = getattr(request.state, "user_role", None) or session_role(op_hub_session)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Reader sessions can't trigger mutating endpoints. "
                "Sign out and log in with the admin password to make "
                "this change."
            ),
        )


def require_admin(
    request: Request,
    op_hub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Admin-only gate for operator *pages* (Status, Services, Logs,
    Resources, Stack, Quests, GrimoireLab Projects).

    Unlike :func:`require_writable` this ignores ``HUB_READONLY`` — those
    pages are read-only views an admin still needs on a locked-down deploy;
    the distinction here is purely role (admin vs reader). Readers get a 403
    so the operator surface is invisible to a viewer even by direct URL, not
    just hidden in the nav.

    Compose AFTER ``require_auth`` (which stamps ``request.state.user_role``);
    falls back to the session cookie so it also works mounted alone.
    """
    role = getattr(request.state, "user_role", None) or session_role(op_hub_session)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This page is operator-only. Sign in with the admin password "
                "to view it."
            ),
        )
