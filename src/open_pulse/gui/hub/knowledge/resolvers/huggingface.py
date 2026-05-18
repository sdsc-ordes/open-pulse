"""huggingface.co resolver — models, datasets, spaces, orgs.

The path shape mirrors GitHub. ``datasets/`` and ``spaces/`` prefixes
flip the kind + collection; bare ``<owner>/<model>`` lands on
``hf_models``. Standalone ``<owner>`` is treated as an org/user
landing.
"""

from __future__ import annotations

from ..entity import Entity
from ..normalize import HubRef
from . import base

_HEAD_TO_KIND = {
    "datasets": ("HuggingFace dataset", ["hf_datasets"]),
    "spaces": ("HuggingFace space", ["hf_spaces"]),
}


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    if not ref.path:
        return None
    parts = ref.path.split("/")
    head = parts[0].lower()

    if head in _HEAD_TO_KIND:
        kind, collections = _HEAD_TO_KIND[head]
        # bigscience/roots looks cleaner as a title than "datasets/bigscience/roots".
        title = "/".join(parts[1:]) if len(parts) > 1 else ref.path
    elif len(parts) == 1:
        kind, collections = "HuggingFace organization or user", ["hf_orgs"]
        title = parts[0]
    else:
        kind, collections = "HuggingFace model", ["hf_models"]
        title = "/".join(parts[:2])  # author/model

    canonical = f"https://huggingface.co/{ref.path}"
    entity = base.build_entity(
        HubRef(host=ref.host, path=ref.path, canonical_url=canonical),
        collections=collections,
        kind=kind,
        title_fallback=title,
        on_status=on_status,
        # Keep the author/model slug rather than promoting bare ``name``.
        title_strategy=(),
    )
    if entity is not None and "/" not in entity.title and len(parts) > 1:
        entity.title = title
    return entity
