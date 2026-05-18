"""infoscience.epfl.ch resolver — EPFL publications, theses, lab pages.

Paths are entity-typed (``entities/publication/<uuid>``,
``entities/person/<uuid>``, ``entities/organization/<uuid>``).
``infoscience_*`` collections key on the UUID payload field
(``article_uuid`` / ``person_uuid`` / …) and also store the full
``infoscience_url`` so URL-based lookup works too.
"""

from __future__ import annotations

from ..entity import Entity
from ..normalize import HubRef
from . import base

_KIND_BY_TYPE = {
    "publication": (
        "EPFL publication",
        ["infoscience_articles", "infoscience_chunks"],
    ),
    "person": ("EPFL researcher", ["infoscience_persons"]),
    "organization": ("EPFL organization", ["infoscience_organizations"]),
}

_DEFAULT_COLLECTIONS = [
    "infoscience_articles",
    "infoscience_chunks",
    "infoscience_persons",
    "infoscience_organizations",
]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    if not ref.path:
        return None
    parts = ref.path.split("/")
    kind = "EPFL Infoscience entity"
    collections = _DEFAULT_COLLECTIONS

    if len(parts) >= 2 and parts[0].lower() == "entities":
        meta = _KIND_BY_TYPE.get(parts[1].lower())
        if meta is not None:
            kind, collections = meta

    canonical = f"https://infoscience.epfl.ch/{ref.path}"
    return base.build_entity(
        HubRef(host=ref.host, path=ref.path, canonical_url=canonical),
        collections=collections,
        kind=kind,
        title_fallback=ref.display,
        on_status=on_status,
    )
