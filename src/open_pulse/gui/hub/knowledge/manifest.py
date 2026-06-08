"""Consume the GME federated index manifest.

The git-metadata-extractor (GME) publishes a machine-readable manifest of
its index stores — the contract consumers build against so they never have
to infer a store's shape from its name or filename. See the GME's
``docs/architecture/index-store-naming.md``. Each entry::

    {
      "name": "zenodo_communities",      # registry key == <name>.duckdb
      "duckdb": "zenodo_communities.duckdb",
      "entity_types": ["community"],
      "backend": "duckdb",               # "vector" = own Qdrant collection
      "surface_as_source": true,         # show as a Hub "Sources" tile
      "id_shape": "url"                  # v3.0.0: every id is a canonical URL
    }

Defaults when a field is omitted: ``backend="vector"``,
``surface_as_source=False``, ``id_shape="url"``.

Delivery: the GME serves this over its v2 HTTP API; the Hub fetches it
server-side using the same in-network base URL + bearer the
``/api/extractor/v2`` proxy already relies on (see ``routes/extractor.py``).

The fetch is **best-effort by design**: a missing endpoint, an unreachable
GME, or a malformed payload all degrade to ``[]`` so the Hub keeps working
on its Qdrant-only behaviour. That lets the consumer ship before the GME
endpoint lands — the manifest-driven tiles simply appear once it does.

Today the Hub uses the manifest for exactly one thing: surfacing the
DuckDB-only stores the GME explicitly allowlists (``surface_as_source``)
as extra "Sources" tiles. Vector-backed stores already tile via their
Qdrant collection, so they need nothing from here.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


# Mirror routes/extractor.py so server-side manifest fetches use the same
# in-network base URL + bearer the v2 proxy already relies on.
def _extractor_base() -> str:
    return os.environ.get(
        "HUB_EXTRACTOR_URL", "http://git-metadata-extractor:1234"
    ).rstrip("/")


def _extractor_token() -> str:
    return os.environ.get("EXTRACTOR_API_TOKEN", "")


# GME endpoint that serves the federated manifest — the HTTP form of
# ``python -m src.index._federated.manifest``. Overridable so a path change
# upstream doesn't need a code change here.
_MANIFEST_PATH = os.environ.get("HUB_MANIFEST_PATH", "/v2/manifest")
_MANIFEST_TIMEOUT = 5.0
_MANIFEST_TTL_SECONDS = 300.0

_VALID_BACKENDS = {"vector", "duckdb"}

# Cache: ``entries`` stays ``None`` until the first fetch attempt, then
# holds the (possibly empty) normalised list. We cache failures too so a
# missing endpoint isn't re-probed on every page view.
_CACHE: dict[str, Any] = {"at": 0.0, "entries": None}
_LOCK = threading.Lock()


def _fresh(now: float) -> bool:
    return (
        _CACHE["entries"] is not None
        and now - float(_CACHE["at"]) < _MANIFEST_TTL_SECONDS
    )


def _normalise(raw: Any) -> list[dict[str, Any]]:
    """Coerce the raw payload into clean entries with all keys present.

    Tolerates a bare list or an ``{"entries": [...]}`` / ``{"stores": [...]}``
    envelope, drops anything without a ``name``, and fills the documented
    defaults so callers can rely on every key existing.
    """
    if isinstance(raw, dict):
        raw = raw.get("entries") or raw.get("stores") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        name = str(e["name"])
        backend = str(e.get("backend") or "vector")
        if backend not in _VALID_BACKENDS:
            backend = "vector"
        out.append(
            {
                "name": name,
                "duckdb": str(e.get("duckdb") or f"{name}.duckdb"),
                "entity_types": [str(t) for t in (e.get("entity_types") or [])],
                "backend": backend,
                "surface_as_source": bool(e.get("surface_as_source", False)),
                "id_shape": str(e.get("id_shape") or "url"),
            }
        )
    return out


def fetch_manifest(*, force: bool = False) -> list[dict[str, Any]]:
    """Return the GME index manifest, TTL-cached. ``[]`` on any failure.

    Best-effort: a missing endpoint / unreachable GME / bad payload all
    degrade to ``[]`` so the Hub keeps working without the manifest channel.
    """
    now = time.monotonic()
    if not force and _fresh(now):
        return list(_CACHE["entries"])

    with _LOCK:
        now = time.monotonic()
        if not force and _fresh(now):
            return list(_CACHE["entries"])

        entries: list[dict[str, Any]] = []
        url = f"{_extractor_base()}{_MANIFEST_PATH}"
        token = _extractor_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            # ``?sources=true`` → only stores the GME marks as Hub tiles
            # (vector-backed + allowlisted DuckDB-only). We still keep just
            # the DuckDB-only ones in surfaced_duckdb_stores() since
            # vector-backed stores already tile via their Qdrant collection.
            r = httpx.get(
                url,
                params={"sources": "true"},
                headers=headers,
                timeout=_MANIFEST_TIMEOUT,
            )
            if r.status_code == 200:
                entries = _normalise(r.json())
            else:
                log.info(
                    "manifest fetch %s -> HTTP %s; using empty manifest",
                    url,
                    r.status_code,
                )
        except (httpx.HTTPError, ValueError) as exc:
            log.info("manifest fetch failed (%s); using empty manifest", exc)

        _CACHE["at"] = time.monotonic()
        _CACHE["entries"] = entries
        return list(entries)


def surfaced_duckdb_stores() -> list[dict[str, Any]]:
    """Manifest stores the Hub should tile that have no Qdrant collection.

    i.e. DuckDB-only (``backend == "duckdb"``) stores the GME explicitly
    allowlists via ``surface_as_source``. Vector-backed stores already tile
    through their Qdrant collection, so they are deliberately excluded here.
    """
    return [
        e
        for e in fetch_manifest()
        if e["backend"] == "duckdb" and e["surface_as_source"]
    ]
