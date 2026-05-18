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


def resolve(ref: HubRef, on_status: StatusCallback | None = None) -> Entity | None:
    """Look up the entity for ``ref``. Returns None when no store knows it.

    ``on_status`` (if supplied) is forwarded to the resolver and to
    :func:`base.build_entity`, which emits one message per lookup step.
    The SSE endpoint uses this to stream live status to the browser.
    """
    if not ref.is_known_host:
        return None

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
