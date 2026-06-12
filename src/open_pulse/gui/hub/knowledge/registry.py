"""Host → resolver dispatch.

Each resolver knows one host family and returns an :class:`Entity`.
Unknown hosts fall through to the generic resolver, which still does
its best across the three stores using URL-as-key matching.

Resolvers are registered at import time; adding a new host is a matter
of writing a module under ``resolvers/`` and adding it to
:data:`_RESOLVERS`. The registry is intentionally tiny — no plugin
discovery, no entry-points — because every resolver in v1 ships with
the hub package.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from .entity import Entity
from .normalize import HubRef
from .resolvers import generic, github, gitlab, huggingface, infoscience, ror, zenodo

StatusCallback = Callable[[str], None]
ResolverFn = Callable[..., Entity | None]


_RESOLVERS: tuple[tuple[str, ResolverFn], ...] = (
    ("github.com", github.resolve),
    ("gitlab.com", gitlab.resolve),
    ("zenodo.org", zenodo.resolve),
    ("ror.org", ror.resolve),
    ("infoscience.epfl.ch", infoscience.resolve),
    ("huggingface.co", huggingface.resolve),
)


# ── resolved-entity cache ──────────────────────────────────────────────────
# Resolving one URL is a 1–30s fan-out across SPARQL / Neo4j / Qdrant plus an
# LLM narrative. The data behind a given URL barely moves between page views,
# so a short-lived in-process cache turns the *second* visit (and every other
# concurrent viewer) into an instant render. Only successful resolutions are
# cached — a miss stays uncached so the wanted-list keeps counting hits and a
# freshly-crawled entity shows up on the next visit.
_CACHE_TTL = 600.0  # seconds
_CACHE_MAX = 256
_cache: OrderedDict[str, tuple[float, Entity]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: str) -> Entity | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        ts, entity = hit
        if time.monotonic() - ts > _CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return entity


def _cache_put(key: str, entity: Entity) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), entity)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    """Drop every cached resolution (used by tests / manual refresh)."""
    with _cache_lock:
        _cache.clear()


def resolve(ref: HubRef, on_status: StatusCallback | None = None) -> Entity | None:
    """Look up the entity for ``ref``. Returns None when no store knows it.

    ``on_status`` (if supplied) is forwarded to the resolver and to
    :func:`base.build_entity`, which emits one message per lookup step.
    The SSE endpoint uses this to stream live status to the browser.

    Successful resolutions are memoised for ``_CACHE_TTL`` seconds so repeat
    views render instantly.
    """
    if not ref.is_known_host:
        return None

    cached = _cache_get(ref.canonical_url)
    if cached is not None:
        if on_status:
            on_status("Served from cache")
        return cached

    entity = _resolve_uncached(ref, on_status)
    if entity is not None:
        _cache_put(ref.canonical_url, entity)
    return entity


def _resolve_uncached(
    ref: HubRef, on_status: StatusCallback | None
) -> Entity | None:
    if on_status:
        on_status(f"Dispatching to resolver for {ref.host}")

    for host, fn in _RESOLVERS:
        if ref.host == host:
            # A host-specific resolver already probes every collection
            # it cares about plus the same SPARQL/Neo4j tables the
            # generic fallback would hit — re-running those would
            # double the wait on a miss. Trust its verdict.
            return fn(ref, on_status=on_status)

    if on_status:
        on_status("No host-specific resolver — falling back to generic")
    return generic.resolve(ref, on_status=on_status)
