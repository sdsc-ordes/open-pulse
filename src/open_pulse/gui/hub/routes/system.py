"""System observability API — ``/api/v1/system/*``.

The first endpoint of the v1 ``system`` group: a readout of legacy-path
deprecation traffic, so an operator can see what still hits the pre-v1 surface
before the sunset date and know when a shim is safe to delete.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth import require_admin, require_auth
from ..deprecation import LEGACY_MAP, SUNSET, hits

router = APIRouter(prefix="/api/v1/system", tags=["system"])


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
