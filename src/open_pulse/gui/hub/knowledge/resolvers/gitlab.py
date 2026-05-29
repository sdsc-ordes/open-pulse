"""gitlab.com resolver — projects, groups, users.

GitLab projects share the schema:SoftwareSourceCode shape with
GitHub but the URL paths nest arbitrarily deep
(``group/subgroup/.../project``). No dedicated qdrant collection
today; ``renkulab_projects`` happens to overlap when projects mirror
to Renku and is included as a soft secondary source.
"""

from __future__ import annotations

from ..entity import Entity
from ..normalize import HubRef
from . import base

_COLLECTIONS = ["renkulab_projects"]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    if not ref.path:
        return None
    canonical = f"https://gitlab.com/{ref.path}"
    entity = base.build_entity(
        HubRef(host=ref.host, path=ref.path, canonical_url=canonical),
        collections=_COLLECTIONS,
        kind="GitLab project",
        title_fallback=ref.path,
        on_status=on_status,
        title_strategy=(),
    )
    if entity is not None and "/" not in entity.title:
        entity.title = ref.path
    return entity
