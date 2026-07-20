"""System observability API — ``/api/v1/system/*``.

Two endpoints:

* ``/deprecations`` — a readout of legacy-path deprecation traffic, so an
  operator can see what still hits the pre-v1 surface before the sunset date.
* ``/store-auth`` — the auth checkpoint the SPARQL reverse-proxy (Caddy
  ``forward_auth``) delegates to, so a single reader identity — every hub
  reader token, plus the store's own creds — is honoured both at the hub API
  and directly at ``/sparql/query``.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from .. import tokens
from ..auth import _match_credential, require_admin, require_auth
from ..deprecation import LEGACY_MAP, SUNSET, hits

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _basic_password(header: str | None) -> str | None:
    """The password from a ``Basic`` Authorization header (username ignored,
    matching the hub's password-only model), or ``None``."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return None
    return raw.split(":", 1)[1] if ":" in raw else raw


def _store_passwords() -> set[str]:
    """The store's own Basic-auth passwords (``user/pass`` env pairs). These
    keep working directly so the hub's OWN internal reader connection — and any
    pre-existing consumer using the raw store cred — is not broken when the
    proxy starts delegating read-auth here."""
    out: set[str] = set()
    for var in ("SPARQL_READER_AUTH", "SPARQL_AUTH"):
        val = os.environ.get(var, "")
        if "/" in val:
            out.add(val.split("/", 1)[1])
    return out


@router.api_route(
    "/store-auth", methods=["GET", "POST"], include_in_schema=False
)
def store_auth(request: Request) -> Response:
    """Auth checkpoint for the SPARQL proxy's ``forward_auth``.

    ``204`` if the Basic password is a valid hub reader/admin credential (env
    reader, admin, or any DB reader token) OR one of the store's own passwords;
    ``401`` otherwise. Full store access — per-token named-graph scope is only
    enforced on the hub's own ``/api/v1/query`` path, by design. A DB token's
    direct-proxy use is logged to its activity like any hub call."""
    pw = _basic_password(request.headers.get("authorization"))
    if pw:
        matched = _match_credential(pw)
        if matched is not None:
            _role, token_id = matched
            if token_id is not None:
                uri = request.headers.get("x-forwarded-uri", "/query")
                method = request.headers.get("x-forwarded-method", "GET")
                tokens.log_access(token_id, method, f"/sparql{uri}")
            return Response(status_code=204)
        if pw in _store_passwords():
            return Response(status_code=204)
    return Response(
        status_code=401, headers={"WWW-Authenticate": 'Basic realm="sparql"'}
    )


@router.get(
    "/deprecations", dependencies=[Depends(require_auth), Depends(require_admin)]
)
def deprecations() -> dict[str, Any]:
    """Legacy-path hit counts + the active mappings, for watching traffic
    drain ahead of the sunset date."""
    return {
        "sunset": SUNSET,
        "mappings": [
            {"legacy": legacy, "canonical": canonical}
            for legacy, canonical in LEGACY_MAP
        ],
        "hits": hits(),
    }
